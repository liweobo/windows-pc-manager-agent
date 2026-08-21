"""Deterministic Windows Installer exit-code classification."""

from __future__ import annotations

from pc_manager_agent.domain.software_uninstall_execution import MsiInstallerResultCategory


def map_msi_exit_code(exit_code: int) -> MsiInstallerResultCategory:
    """Map documented Windows Installer/system codes without guessing root causes."""
    return {
        0: MsiInstallerResultCategory.SUCCESS,
        5: MsiInstallerResultCategory.PRIVILEGE_REQUIRED,
        1602: MsiInstallerResultCategory.USER_CANCELLED,
        1605: MsiInstallerResultCategory.PRODUCT_NOT_INSTALLED,
        1614: MsiInstallerResultCategory.PRODUCT_NOT_INSTALLED,
        1618: MsiInstallerResultCategory.ANOTHER_INSTALL_IN_PROGRESS,
        1625: MsiInstallerResultCategory.POLICY_BLOCKED,
        1641: MsiInstallerResultCategory.REBOOT_INITIATED_UNEXPECTED,
        3010: MsiInstallerResultCategory.SUCCESS_REBOOT_REQUIRED,
    }.get(exit_code, _fallback(exit_code))


def _fallback(exit_code: int) -> MsiInstallerResultCategory:
    if 1601 <= exit_code <= 1654:
        return MsiInstallerResultCategory.INSTALLER_FAILURE
    return MsiInstallerResultCategory.UNKNOWN
