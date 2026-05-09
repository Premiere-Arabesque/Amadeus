from __future__ import annotations

from pydantic import BaseModel


class ContactEntry(BaseModel):
    name: str
    recipient_id: str = "default-user"
    channel: str = "api"
    kind: str = "user"
    enabled: bool = True


class ContactBook:
    def __init__(self, contacts: list[ContactEntry] | None = None) -> None:
        self._contacts: dict[str, ContactEntry] = {}
        for contact in contacts or []:
            self.upsert(contact)

    def clear(self) -> None:
        self._contacts.clear()

    def list_contacts(self) -> list[ContactEntry]:
        return sorted(
            [contact for contact in self._contacts.values() if contact.enabled],
            key=lambda contact: contact.name.casefold(),
        )

    def replace_contacts(self, contacts: list[ContactEntry]) -> None:
        self.clear()
        for contact in contacts:
            self.upsert(contact)

    def upsert(self, contact: ContactEntry) -> ContactEntry:
        self._contacts[self._key(contact.name)] = contact.model_copy(deep=True)
        return self._contacts[self._key(contact.name)]

    def remember_user(
        self,
        *,
        name: str,
        recipient_id: str,
        channel: str,
    ) -> ContactEntry:
        existing = self.resolve(name)
        contact = ContactEntry(
            name=name.strip() or "用户",
            recipient_id=recipient_id.strip()
            or (existing.recipient_id if existing is not None else "default-user"),
            channel=channel.strip() or (existing.channel if existing is not None else "api"),
            kind="user",
            enabled=True,
        )
        return self.upsert(contact)

    def resolve(self, name: str) -> ContactEntry | None:
        if not name.strip():
            return None
        return self._contacts.get(self._key(name))

    @staticmethod
    def _key(name: str) -> str:
        return name.strip().casefold()
