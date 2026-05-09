from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pydantic_core import SchemaValidator, core_schema
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, ToolsetTool

from app.core.outcomes import OutcomeStatus
from app.core.types import JsonValue
from app.tool.models import (
    ActionResult,
    ToolCollectionSpec,
    ToolCollectionType,
    ToolExecutor,
    ToolSourceType,
    ToolSpec,
)

_ANY_VALIDATOR = SchemaValidator(core_schema.any_schema())


@dataclass
class _RegisteredCollection:
    spec: ToolCollectionSpec
    toolset: AbstractToolset[Any]
    tool_names: set[str]


@dataclass
class _RegisteredToolBinding:
    spec: ToolSpec
    collection: ToolCollectionSpec
    toolset: AbstractToolset[Any]
    tool: ToolsetTool[Any]


class _ExecutorToolset(AbstractToolset[None]):
    def __init__(
        self,
        collection: ToolCollectionSpec,
        entries: list[tuple[ToolSpec, ToolExecutor]],
    ) -> None:
        self.collection = collection
        self._executors = {spec.name: executor for spec, executor in entries}
        self._tools = {
            spec.name: ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=spec.name,
                    description=spec.description,
                    parameters_json_schema=_tool_json_schema(spec.required_arguments),
                    metadata={
                        **dict(spec.metadata),
                        "source_type": spec.source_type.value,
                        "source_id": spec.source_id,
                        "collection_id": collection.collection_id,
                        "collection_type": collection.collection_type.value,
                    },
                ),
                max_retries=0,
                args_validator=_ANY_VALIDATOR,
            )
            for spec, _ in entries
        }

    @property
    def id(self) -> str | None:
        return self.collection.collection_id

    async def get_tools(self, ctx) -> dict[str, ToolsetTool[None]]:
        del ctx
        return dict(self._tools)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx,
        tool: ToolsetTool[None],
    ) -> ActionResult:
        del ctx, tool
        executor = self._executors[name]
        return await executor(cast(dict[str, JsonValue], tool_args))


class ToolRegistry:
    def __init__(self) -> None:
        self._collections: dict[str, _RegisteredCollection] = {}
        self._tools: dict[str, _RegisteredToolBinding] = {}

    def register(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        collection = ToolCollectionSpec(
            collection_id=spec.collection_id or spec.source_id or f"single:{spec.name}",
            name=spec.collection_name or spec.name,
            description=spec.description,
            collection_type=spec.collection_type or ToolCollectionType.SINGLE,
            source_type=spec.source_type,
            source_id=spec.source_id,
            metadata=dict(spec.metadata),
        )
        normalized_spec = spec.model_copy(
            update={
                "collection_id": collection.collection_id,
                "collection_name": collection.name,
                "collection_type": collection.collection_type,
            }
        )
        toolset = _ExecutorToolset(collection, [(normalized_spec, executor)])
        self._register_bound_tools(
            collection=collection,
            toolset=toolset,
            bindings=[
                _RegisteredToolBinding(
                    spec=normalized_spec,
                    collection=collection,
                    toolset=toolset,
                    tool=(toolset._tools[normalized_spec.name]),
                )
            ],
        )

    def register_collection(
        self,
        collection: ToolCollectionSpec,
        entries: list[tuple[ToolSpec, ToolExecutor]],
    ) -> None:
        normalized_entries = [
            (
                spec.model_copy(
                    update={
                        "collection_id": collection.collection_id,
                        "collection_name": collection.name,
                        "collection_type": collection.collection_type,
                    }
                ),
                executor,
            )
            for spec, executor in entries
        ]
        toolset = _ExecutorToolset(collection, normalized_entries)
        bindings = [
            _RegisteredToolBinding(
                spec=spec,
                collection=collection,
                toolset=toolset,
                tool=(toolset._tools[spec.name]),
            )
            for spec, _ in normalized_entries
        ]
        self._register_bound_tools(collection=collection, toolset=toolset, bindings=bindings)

    async def register_toolset(
        self,
        collection: ToolCollectionSpec,
        toolset: AbstractToolset[Any],
    ) -> None:
        tools = await toolset.get_tools(cast(Any, None))
        bindings: list[_RegisteredToolBinding] = []
        for name, tool in tools.items():
            bindings.append(
                _RegisteredToolBinding(
                    spec=_tool_spec_from_toolset_tool(
                        tool_name=name,
                        tool=tool,
                        collection=collection,
                    ),
                    collection=collection,
                    toolset=toolset,
                    tool=tool,
                )
            )
        self._register_bound_tools(collection=collection, toolset=toolset, bindings=bindings)

    def get_tool(self, name: str) -> ToolSpec | None:
        binding = self._tools.get(name)
        return binding.spec if binding is not None else None

    def get_descriptor(self, capability: str) -> ToolSpec | None:
        return self.get_tool(capability)

    def get_collection(self, collection_id: str) -> ToolCollectionSpec | None:
        collection = self._collections.get(collection_id)
        return collection.spec if collection is not None else None

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def capability_names(self) -> list[str]:
        return self.tool_names()

    def list_tools(self) -> list[ToolSpec]:
        return [self._tools[name].spec for name in self.tool_names()]

    def list_collections(self) -> list[ToolCollectionSpec]:
        return [self._collections[name].spec for name in sorted(self._collections)]

    def combined_toolset(self) -> CombinedToolset[Any]:
        return CombinedToolset([entry.toolset for entry in self._collections.values()])

    async def invoke(self, name: str, arguments: dict[str, JsonValue]) -> ActionResult:
        binding = self._tools.get(name)
        if binding is None:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary=f"The tool {name!r} is not registered.",
                raw={"tool": name, "arguments": arguments},
            )

        missing = [
            argument
            for argument in binding.spec.required_arguments
            if argument not in arguments or _is_missing_argument_value(arguments[argument])
        ]
        if missing:
            return ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary=(
                    f"The tool {name!r} is missing required arguments: "
                    f"{', '.join(missing)}."
                ),
                raw={"tool": name, "arguments": arguments, "missing": missing},
            )

        result = await binding.toolset.call_tool(
            name,
            arguments,
            cast(Any, None),
            binding.tool,
        )
        if isinstance(result, ActionResult):
            return result
        return ActionResult(
            status=OutcomeStatus.SUCCESS,
            summary=str(result),
            raw={"tool": name, "arguments": arguments, "result": _json_safe(result)},
        )

    def _register_bound_tools(
        self,
        *,
        collection: ToolCollectionSpec,
        toolset: AbstractToolset[Any],
        bindings: list[_RegisteredToolBinding],
    ) -> None:
        if collection.collection_id in self._collections:
            raise ValueError(f"Tool collection {collection.collection_id!r} is already registered.")
        for binding in bindings:
            if binding.spec.name in self._tools:
                raise ValueError(f"Tool {binding.spec.name!r} is already registered.")

        self._collections[collection.collection_id] = _RegisteredCollection(
            spec=collection,
            toolset=toolset,
            tool_names={binding.spec.name for binding in bindings},
        )
        for binding in bindings:
            self._tools[binding.spec.name] = binding


def _tool_spec_from_toolset_tool(
    *,
    tool_name: str,
    tool: ToolsetTool[Any],
    collection: ToolCollectionSpec,
) -> ToolSpec:
    schema = tool.tool_def.parameters_json_schema
    required_arguments = schema.get("required", []) if isinstance(schema, dict) else []
    if not isinstance(required_arguments, list):
        required_arguments = []
    return ToolSpec(
        name=tool_name,
        description=tool.tool_def.description or tool_name,
        required_arguments=[str(item) for item in required_arguments],
        source_type=collection.source_type,
        source_id=collection.source_id,
        collection_id=collection.collection_id,
        collection_name=collection.name,
        collection_type=collection.collection_type,
        metadata={
            **dict(collection.metadata),
            "input_schema": _json_safe(schema if isinstance(schema, dict) else {}),
            "toolset_metadata": _json_safe(tool.tool_def.metadata),
        },
    )


def _tool_json_schema(required_arguments: list[str]) -> dict[str, JsonValue]:
    properties = {argument: {"type": "string"} for argument in required_arguments}
    return {
        "type": "object",
        "properties": properties,
        "required": required_arguments,
        "additionalProperties": True,
    }


def _json_safe(value: object) -> JsonValue:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_missing_argument_value(value: JsonValue) -> bool:
    return value is None or value == ""
