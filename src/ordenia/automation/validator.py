"""Validation of immutable organization plans; never performs file operations."""

import os
from dataclasses import replace
from pathlib import Path

from ordenia.core.destinations import destination_directory, destination_root, validate_relative_destination
from ordenia.core.file_utils import path_key
from ordenia.database.repositories import Repository
from ordenia.platform.actions import default_central_root

from .models import OrganizationPlan, PLAN_SCHEMA_VERSION, PlanStatus, PlanWarning, RiskLevel
from .policy import AutomationPolicy


class PlanValidationError(ValueError):
    def __init__(self, issues: tuple[PlanWarning, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


class PlanValidator:
    def __init__(self, repository: Repository, policy: AutomationPolicy | None = None) -> None:
        self.repository = repository
        self.policy = policy

    def _policy(self) -> AutomationPolicy:
        if self.policy is not None:
            return self.policy
        return AutomationPolicy(protected_paths=self.repository.plans.list_protected_paths())

    def _trusted_root(self, folder_id: int) -> Path:
        folder = self.repository.get_folder(folder_id)
        central = Path(self.repository.get_setting("central_destination", str(default_central_root())))
        return destination_root(folder.path, folder.destination_strategy, central, folder.custom_destination)

    def validate(self, plan: OrganizationPlan, *, expected_revision: int | None = None) -> OrganizationPlan:
        issues: list[PlanWarning] = []
        warnings = list(plan.warnings)
        if not plan.id.strip():
            issues.append(PlanWarning("invalid_plan_id", "El plan debe tener un identificador."))
        if plan.revision < 1:
            issues.append(PlanWarning("invalid_revision", "La revisión del plan debe ser mayor que cero."))
        if expected_revision is not None and plan.revision != expected_revision:
            issues.append(PlanWarning("stale_revision", "La revisión del plan ya no coincide con la esperada."))
        stored_revision = self.repository.plans.current_revision(plan.id)
        if stored_revision is not None and stored_revision != plan.revision:
            issues.append(PlanWarning("stale_revision", "Existe una revisión más reciente del plan."))
        if plan.schema_version != PLAN_SCHEMA_VERSION:
            issues.append(PlanWarning("unsupported_schema", "La versión del esquema del plan no es compatible."))

        seen_ids: set[int] = set()
        destinations: dict[str, int] = {}
        validated_items = []
        policy = self._policy()
        for item in plan.items:
            if item.file_id <= 0:
                issues.append(PlanWarning("invalid_file_id", "El identificador de archivo no es válido.", item.file_id))
                continue
            if item.file_id in seen_ids:
                issues.append(PlanWarning("duplicate_file", "El mismo archivo aparece más de una vez en el plan.", item.file_id))
                continue
            seen_ids.add(item.file_id)
            try:
                file = self.repository.get_file(item.file_id)
                folder = self.repository.get_folder(file.watched_folder_id)
            except LookupError:
                issues.append(PlanWarning("unknown_file", "El archivo o su carpeta vigilada ya no existe en el índice.", item.file_id))
                continue

            if file.index_state != "active":
                issues.append(PlanWarning("inactive_record", "El archivo ya no está activo en el índice.", item.file_id))
            if path_key(item.source) != path_key(file.path):
                issues.append(PlanWarning("changed_path", "La ruta actual ya no coincide con la incluida en el plan.", item.file_id))
            if (item.fingerprint_size, item.fingerprint_mtime_ns) != (file.size, file.mtime_ns):
                issues.append(PlanWarning("stale_fingerprint", "El archivo cambió desde que se creó el plan.", item.file_id))
            try:
                current = file.path.stat()
            except OSError:
                current = None
            if current is not None and (current.st_size, current.st_mtime_ns) != (file.size, file.mtime_ns):
                issues.append(PlanWarning("stale_fingerprint", "El archivo físico cambió desde su último registro.", item.file_id))

            decision = policy.evaluate(file, folder)
            if decision.level is RiskLevel.PROTECTED:
                issues.append(PlanWarning(decision.code, decision.reason, item.file_id))
            elif decision.level is RiskLevel.REVIEW_REQUIRED:
                warnings.append(PlanWarning(decision.code, decision.reason, item.file_id))

            try:
                relative = validate_relative_destination(item.relative_destination)
                root = self._trusted_root(folder.id)
                root_decision = policy.evaluate_destination_root(root)
                if root_decision.level is RiskLevel.PROTECTED:
                    issues.append(PlanWarning(root_decision.code, root_decision.reason, item.file_id))
                directory = destination_directory(root, relative)
                destination = directory / file.name
                key = os.path.normcase(os.path.abspath(destination))
                if key in destinations:
                    issues.append(PlanWarning(
                        "destination_collision",
                        f"Dos elementos del plan apuntan al mismo destino: {destination}",
                        item.file_id,
                    ))
                else:
                    destinations[key] = item.file_id
                validated_items.append(replace(
                    item, relative_destination=relative, risk_level=decision.level,
                ))
            except ValueError as exc:
                issues.append(PlanWarning("unsafe_destination", str(exc), item.file_id))

        if issues:
            raise PlanValidationError(tuple(issues))
        unique_warnings = tuple(dict.fromkeys(warnings))
        return replace(plan, status=PlanStatus.VALIDATED, items=tuple(validated_items), warnings=unique_warnings)
