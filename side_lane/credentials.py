from __future__ import annotations

import ctypes
from ctypes import wintypes
import getpass
import platform
import subprocess
from typing import Mapping


class CredentialError(Exception):
    pass


PROVIDER_PREFIXES = ("ANTHROPIC_", "OPENAI_", "OPENROUTER_", "ZAI_", "ZHIPUAI_")


def scrub_provider_environment(inherited: Mapping[str, str]) -> dict[str, str]:
    """Remove transport credentials while retaining ordinary same-user authority."""
    return {
        name: value
        for name, value in inherited.items()
        if not name.startswith(PROVIDER_PREFIXES)
    }


def _macos_read(service: str, *, reveal: bool) -> str | bool:
    command = ["security", "find-generic-password", "-a", getpass.getuser(), "-s", service]
    if reveal:
        command.append("-w")
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if reveal else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if not reveal:
        return result.returncode == 0
    value = result.stdout.rstrip("\r\n") if result.returncode == 0 else ""
    if not value:
        raise CredentialError(f"credential absent for service {service}")
    return value


def _windows_read(service: str, *, reveal: bool) -> str | bool:
    if not hasattr(ctypes, "windll"):
        raise CredentialError("Windows Credential Manager is unavailable")

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
        ]

    pointer = ctypes.POINTER(Credential)()
    ok = ctypes.windll.advapi32.CredReadW(service, 1, 0, ctypes.byref(pointer))
    if not ok:
        if reveal:
            raise CredentialError(f"credential absent for service {service}")
        return False
    try:
        if not reveal:
            return True
        item = pointer.contents
        value = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize).decode("utf-16-le")
        if not value:
            raise CredentialError(f"credential absent for service {service}")
        return value
    finally:
        ctypes.windll.advapi32.CredFree(pointer)


def credential_present(service: str, system: str | None = None) -> bool:
    selected = system or platform.system()
    if selected == "Darwin":
        return bool(_macos_read(service, reveal=False))
    if selected == "Windows":
        return bool(_windows_read(service, reveal=False))
    return False


def read_credential(service: str, system: str | None = None) -> str:
    selected = system or platform.system()
    if selected == "Darwin":
        return str(_macos_read(service, reveal=True))
    if selected == "Windows":
        return str(_windows_read(service, reveal=True))
    raise CredentialError("supported credential stores are macOS Keychain and Windows Credential Manager")


def selected_provider_environment(
    inherited: Mapping[str, str], provider: str, provider_config: Mapping[str, object],
    model: str, secret: str, host: str,
) -> dict[str, str]:
    child = scrub_provider_environment(inherited)
    if provider == "claude" and host == "claude":
        child["ANTHROPIC_API_KEY"] = secret
    elif provider == "openrouter" and host == "codex":
        child["OPENROUTER_API_KEY"] = secret
    elif provider in {"openrouter", "glm"} and host == "claude":
        child.update({
            "ANTHROPIC_AUTH_TOKEN": secret,
            "ANTHROPIC_BASE_URL": str(provider_config["base_url"]),
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": model,
        })
    else:
        raise CredentialError(f"no credential adapter for {host}/{provider}")
    return child
