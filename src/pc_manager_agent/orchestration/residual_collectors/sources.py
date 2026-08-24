"""Named source-specific collectors with no dynamic discovery surface."""

from __future__ import annotations

from pc_manager_agent.domain.software_residuals import ResidualSource
from pc_manager_agent.orchestration.residual_collectors.base import SourceResidualCollector
from pc_manager_agent.safety.residual_classification import ResidualClassifier
from pc_manager_agent.safety.residual_ownership import ResidualOwnershipEvaluator
from pc_manager_agent.safety.residual_scope_policy import ResidualScanScopePolicy
from pc_manager_agent.safety.user_data_protection import UserDataProtectionPolicy


class InstallLocationResidualCollector(SourceResidualCollector):
    """Inspect exact classic or MSIX installation locations."""

    def __init__(
        self,
        scope: ResidualScanScopePolicy,
        classifier: ResidualClassifier,
        ownership: ResidualOwnershipEvaluator,
        protection: UserDataProtectionPolicy,
    ) -> None:
        super().__init__(
            frozenset({ResidualSource.INSTALL_LOCATION, ResidualSource.MSIX_INSTALL_LOCATION}),
            scope,
            classifier,
            ownership,
            protection,
        )


class KnownAppDataResidualCollector(SourceResidualCollector):
    """Inspect only exact application-data paths captured before uninstall."""

    def __init__(
        self,
        scope: ResidualScanScopePolicy,
        classifier: ResidualClassifier,
        ownership: ResidualOwnershipEvaluator,
        protection: UserDataProtectionPolicy,
    ) -> None:
        super().__init__(
            frozenset({ResidualSource.KNOWN_APP_DATA}),
            scope,
            classifier,
            ownership,
            protection,
        )


class ShortcutResidualCollector(SourceResidualCollector):
    """Inspect exact shortcut files as metadata without resolving or executing them."""

    def __init__(
        self,
        scope: ResidualScanScopePolicy,
        classifier: ResidualClassifier,
        ownership: ResidualOwnershipEvaluator,
        protection: UserDataProtectionPolicy,
    ) -> None:
        super().__init__(
            frozenset({ResidualSource.SHORTCUT}), scope, classifier, ownership, protection
        )


class MsixDataResidualCollector(SourceResidualCollector):
    """Inspect bounded MSIX package-data metadata under an exact Package Family path."""

    def __init__(
        self,
        scope: ResidualScanScopePolicy,
        classifier: ResidualClassifier,
        ownership: ResidualOwnershipEvaluator,
        protection: UserDataProtectionPolicy,
    ) -> None:
        super().__init__(
            frozenset({ResidualSource.MSIX_PACKAGE_DATA}),
            scope,
            classifier,
            ownership,
            protection,
        )


class KnownServiceArtifactCollector(SourceResidualCollector):
    """Inspect only exact service artifact paths captured by earlier evidence."""

    def __init__(
        self,
        scope: ResidualScanScopePolicy,
        classifier: ResidualClassifier,
        ownership: ResidualOwnershipEvaluator,
        protection: UserDataProtectionPolicy,
    ) -> None:
        super().__init__(
            frozenset({ResidualSource.SERVICE_ARTIFACT}),
            scope,
            classifier,
            ownership,
            protection,
        )


class KnownConfigurationResidualCollector(SourceResidualCollector):
    """Inspect exact configuration paths while always applying data protection."""

    def __init__(
        self,
        scope: ResidualScanScopePolicy,
        classifier: ResidualClassifier,
        ownership: ResidualOwnershipEvaluator,
        protection: UserDataProtectionPolicy,
    ) -> None:
        super().__init__(
            frozenset({ResidualSource.CONFIGURATION}),
            scope,
            classifier,
            ownership,
            protection,
        )
