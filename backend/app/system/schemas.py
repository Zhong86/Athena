from pydantic import BaseModel


class ResetResult(BaseModel):
    # Set when the local wipe succeeded but revoking Google upstream or
    # removing its files from the Hermes host did not. The reset still
    # happened; the user should know it was untidy.
    warning: str | None = None
