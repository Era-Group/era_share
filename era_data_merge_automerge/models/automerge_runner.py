# -*- coding: utf-8 -*-
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class DataMergeAutoMergeRunner(models.Model):
    """Runs an auto-merge job for Data Cleaning duplicate groups.

    Why this module exists:
    - ir.cron "code" actions are executed via safe_eval, which may forbid try/except, with, __name__, imports, etc.
    - The cron only runs a thin `model.cron_run()` call; all complex logic stays in real Python methods.
    """

    _name = "data_merge.automerge.runner"
    _description = "Data Cleaning Auto Merge Runner"

    name = fields.Char(default="Auto Merge Runner", readonly=True)

    @api.model
    def _get_param_int(self, key, default):
        val = self.env["ir.config_parameter"].sudo().get_param(key, default)
        try:
            return int(val)
        except Exception:
            return default

    @api.model
    def _get_param_bool(self, key, default=False):
        val = self.env["ir.config_parameter"].sudo().get_param(key, str(bool(default)))
        if isinstance(val, bool):
            return val
        val = (val or "").strip().lower()
        return val in ("1", "true", "yes", "y", "on")

    @api.model
    def _get_param_str(self, key, default=""):
        val = self.env["ir.config_parameter"].sudo().get_param(key, default)
        return (val or default).strip()

    @api.model
    def _try_advisory_lock(self, lock_key: int) -> bool:
        """PostgreSQL advisory lock to prevent concurrent runs."""
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        row = self.env.cr.fetchone()
        return bool(row and row[0])

    @api.model
    def _advisory_unlock(self, lock_key: int) -> None:
        try:
            self.env.cr.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
        except Exception:
            # If unlock fails, don't crash cron.
            pass

    @api.model
    def _model_names_from_module_xmlids(self, modules=None):
        """Return model names referenced by ir.model XML IDs for given modules."""
        modules = modules or ["data_cleaning", "data_merge"]
        names = []
        try:
            rows = self.env["ir.model.data"].sudo().search_read(
                [
                    ("model", "=", "ir.model"),
                    ("module", "in", modules),
                ],
                ["res_id", "name", "module"],
                limit=500,
            )
            if not rows:
                return names
            model_ids = [r["res_id"] for r in rows if r.get("res_id")]
            if not model_ids:
                return names
            models_map = {
                r["id"]: r["model"]
                for r in self.env["ir.model"].sudo().browse(model_ids).read(["model"])
                if r.get("id") and r.get("model")
            }
            for row in rows:
                model_name = models_map.get(row.get("res_id"))
                if model_name:
                    names.append(model_name)
        except Exception:
            return []
        # Deduplicate while preserving order
        uniq = []
        seen = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            uniq.append(name)
        return uniq

    @api.model
    def _is_noise_method(self, method_name):
        lowered = (method_name or "").lower()
        if not lowered:
            return True
        if lowered.startswith("__"):
            return True
        if lowered.startswith(("ai_", "_compute_", "compute_", "_inverse_", "_search_")):
            return True
        if "default" in lowered or "onchange" in lowered:
            return True
        if lowered in {"create", "write", "unlink", "copy", "name_get", "name_search"}:
            return True
        return False

    @api.model
    def _detect_dedup_model_from_cron(self):
        """Fallback: deduce dedup model from built-in Data Cleaning cron."""
        try:
            cron = self.env["ir.cron"].sudo().search(
                [("name", "ilike", "Find Duplicate Records"), ("active", "=", True)],
                limit=1,
            )
            if cron and cron.model_id and cron.model_id.model:
                name = cron.model_id.model
                try:
                    m = self.env[name]
                    fields_map = getattr(m, "_fields", {}) or {}
                    if hasattr(m, "_cron_find_duplicates") or "merge_mode" in fields_map:
                        return m, name
                except Exception:
                    return None, None
        except Exception:
            return None, None
        return None, None

    @api.model
    def _detect_group_model_from_dedup_model(self, dedup_model):
        """Infer group model from dedup model relation fields."""
        if not dedup_model:
            return None, None
        try:
            fields_map = getattr(dedup_model, "_fields", {}) or {}
            relation_candidates = []
            for field_name, field_obj in fields_map.items():
                comodel = getattr(field_obj, "comodel_name", None)
                if not comodel:
                    continue
                field_type = getattr(field_obj, "type", None)
                if field_type not in ("one2many", "many2many", "many2one"):
                    continue
                if any(k in comodel for k in ("merge", "duplicate", "dedup", "clean")):
                    relation_candidates.append((field_name, comodel))
            # Prioritize likely duplicate record collections
            relation_candidates.sort(
                key=lambda item: (
                    0 if item[0] in ("record_ids", "line_ids", "duplicate_ids") else 1,
                    item[0],
                )
            )
            for _, comodel in relation_candidates:
                try:
                    gm = self.env[comodel]
                    if getattr(gm, "_abstract", False):
                        continue
                    if not self._has_merge_capability(gm):
                        continue
                    return gm, comodel
                except Exception:
                    continue
        except Exception:
            return None, None
        return None, None

    @api.model
    def _candidate_data_cleaning_models(self):
        """Collect candidate model names likely related to deduplication/merge."""
        names = []
        for model_name in self._model_names_from_module_xmlids(["data_cleaning", "data_merge"]):
            if model_name == self._name:
                continue
            if model_name.startswith(("data.", "data_")) or any(k in model_name for k in ("merge", "duplicate", "dedup", "clean")):
                names.append(model_name)
        _, from_cron_name = self._detect_dedup_model_from_cron()
        if from_cron_name and from_cron_name not in names:
            names.append(from_cron_name)

        seen = set(names)

        # Add likely related comodels of known data-cleaning models.
        for base_name in list(names):
            try:
                base_model = self.env[base_name]
                fields_map = getattr(base_model, "_fields", {}) or {}
                for field_obj in fields_map.values():
                    comodel = getattr(field_obj, "comodel_name", None)
                    if not comodel or comodel in seen:
                        continue
                    if comodel == self._name:
                        continue
                    if comodel.startswith(("data.", "data_")) or any(k in comodel for k in ("merge", "duplicate", "dedup", "clean")):
                        names.append(comodel)
                        seen.add(comodel)
            except Exception:
                continue

        # Conservative registry fallback: only data* namespace candidates.
        for model_name in sorted(self.env.registry.models.keys()):
            if model_name in seen:
                continue
            if not model_name.startswith(("data.", "data_")):
                continue
            if model_name == self._name:
                continue
            if any(k in model_name for k in ("merge", "duplicate", "dedup", "clean")):
                names.append(model_name)
                seen.add(model_name)
        return names

    @api.model
    def _discover_method_names(self, model, mode="merge", include_generic=False):
        """Discover likely method names on a model for merge/find operations."""
        if mode == "merge":
            preferred = [
                "_cron_merge_duplicates",
                "action_merge_duplicates",
                "merge_duplicates",
                "action_process_duplicates",
                "action_merge_records",
                "merge_records",
            ]
            if include_generic:
                preferred += ["action_merge", "merge", "action_process", "_action_merge", "_merge_records"]
            must_contain = ("merge",)
            any_of = ("duplicate", "dedup", "cron", "record", "action")
        else:
            preferred = [
                "_cron_find_duplicates",
                "action_find_duplicates",
                "find_duplicates",
            ]
            must_contain = ("find",)
            any_of = ("duplicate", "dedup", "cron", "record")

        discovered = []
        for method_name in preferred:
            if self._is_noise_method(method_name):
                continue
            if hasattr(model, method_name):
                discovered.append(method_name)

        # Dynamic discovery for variant Odoo versions/customizations.
        for method_name in dir(model):
            if self._is_noise_method(method_name):
                continue
            if method_name in discovered:
                continue
            lowered = method_name.lower()
            if not all(token in lowered for token in must_contain):
                continue
            if not any(token in lowered for token in any_of):
                continue
            try:
                attr = getattr(model, method_name)
                if callable(attr):
                    discovered.append(method_name)
            except Exception:
                continue
        return discovered

    @api.model
    def _invoke_method_flex(self, target, method_name, batch_size=50):
        """Invoke a discovered method with flexible signatures."""
        method = getattr(target, method_name, None)
        if not method:
            return False

        # `merge_multiple_records` expects a structured mapping argument.
        # Generic invocation is unsafe; prefer singleton merge methods instead.
        if method_name == "merge_multiple_records":
            return False

        # Many merge methods require singleton records (e.g. data_merge.group.merge_records).
        # If called on a multi-recordset, execute per record and aggregate success.
        if hasattr(target, "ids") and len(getattr(target, "ids", [])) > 1 and method_name in (
            "merge_records",
            "action_merge_records",
            "action_merge",
            "merge",
        ):
            any_ok = False
            for rec in target:
                if self._invoke_method_flex(rec, method_name, batch_size=batch_size):
                    any_ok = True
            return any_ok

        # Try common signatures in a controlled order.
        attempts = [
            lambda: method(),
            lambda: method(limit=batch_size),
            lambda: method(batch_size),
            lambda: method(False),
            lambda: method(None),
        ]
        last_type_error = False
        for run in attempts:
            try:
                run()
                return True
            except TypeError:
                last_type_error = True
                continue
            except Exception:
                _logger.exception("Auto-merge: invocation failed for method %s on %s.", method_name, getattr(target, "_name", type(target)))
                return False

        if last_type_error:
            _logger.warning(
                "Auto-merge: invocation signature mismatch for method %s on %s.",
                method_name,
                getattr(target, "_name", type(target)),
            )
        return False

    @api.model
    def _has_merge_capability(self, model):
        return bool(self._discover_method_names(model, mode="merge", include_generic=True))

    @api.model
    def _build_automatic_scope_domain(self, model, only_automatic=True):
        """Build a domain that guarantees automatic-only processing when possible."""
        if not only_automatic:
            return []

        fields_map = getattr(model, "_fields", {}) or {}
        # Direct merge mode on rule model.
        if "merge_mode" in fields_map:
            return [("merge_mode", "=", "automatic")]

        # Relation to dedup rule model.
        relation_candidates = (
            "model_id",
            "merge_model_id",
            "deduplication_model_id",
            "data_merge_model_id",
            "dedup_model_id",
            "rule_id",
        )
        dedup_comodels = {
            "data.merge.model",
            "data_merge.model",
            "data.cleaning.model",
            "data_cleaning.model",
            "data.merge.rule",
            "data_merge.rule",
        }
        for field_name in relation_candidates:
            field_obj = fields_map.get(field_name)
            if not field_obj:
                continue
            comodel = getattr(field_obj, "comodel_name", None)
            if comodel in dedup_comodels:
                return [("%s.merge_mode" % field_name, "=", "automatic")]

        return []

    @api.model
    def _merge_via_capability_scan(self, run_find_first=False, batch_size=50, only_automatic=True):
        """Last fallback: run native merge cron-like methods on candidate models."""
        target_model = self._get_param_str("data_merge_automerge.target_model", "res.partner")
        target_model = (target_model or "").strip()
        target_model_id = None
        if target_model:
            try:
                im = self.env["ir.model"].sudo().search([("model", "=", target_model)], limit=1)
                target_model_id = im.id or None
            except Exception:
                target_model_id = None

        merge_success = 0
        find_success = 0
        attempts = 0
        candidates = self._candidate_data_cleaning_models()
        _logger.info(
            "Auto-merge: capability scan on %s candidate models (target_model=%s, only_automatic=%s).",
            len(candidates),
            target_model or "ANY",
            only_automatic,
        )

        for model_name in candidates:
            try:
                model = self.env[model_name].sudo()
            except Exception:
                continue
            if getattr(model, "_abstract", False):
                continue

            if getattr(model, "_transient", False):
                continue

            merge_methods = self._discover_method_names(model, mode="merge", include_generic=True)
            find_methods = self._discover_method_names(model, mode="find")
            if not merge_methods and not find_methods:
                continue

            _logger.debug(
                "Auto-merge: capability candidate model=%s merge_methods=%s find_methods=%s",
                model_name,
                merge_methods[:10],
                find_methods[:10],
            )

            fields_map = getattr(model, "_fields", {}) or {}
            domain = [("active", "=", True)] if "active" in fields_map else []
            automatic_domain = self._build_automatic_scope_domain(model, only_automatic=only_automatic)
            if only_automatic and not automatic_domain:
                # Don't risk touching manual dedup rules when scope cannot be enforced.
                continue
            domain.extend(automatic_domain)
            # Scope scan to a business model (defaults to contacts/res.partner).
            if target_model and target_model_id:
                if "model_id" in fields_map and getattr(fields_map["model_id"], "comodel_name", "") == "ir.model":
                    domain.append(("model_id", "=", target_model_id))
                elif "res_model_id" in fields_map and getattr(fields_map["res_model_id"], "comodel_name", "") == "ir.model":
                    domain.append(("res_model_id", "=", target_model_id))
            if target_model and "res_model" in fields_map:
                domain.append(("res_model", "=", target_model))

            recs = model.search(domain, limit=batch_size) if ("id" in fields_map or hasattr(model, "search")) else model
            if target_model and not recs:
                continue
            targets = recs if recs else model

            if run_find_first and find_methods:
                for method_name in find_methods:
                    attempts += 1
                    if self._invoke_method_flex(targets, method_name, batch_size=batch_size):
                        find_success += 1
                        _logger.info("Auto-merge: capability scan ran %s on %s.", method_name, model_name)
                        break
                    _logger.warning("Auto-merge: capability scan failed %s on %s.", method_name, model_name)

            for method_name in merge_methods:
                attempts += 1
                if self._invoke_method_flex(targets, method_name, batch_size=batch_size):
                    merge_success += 1
                    _logger.info("Auto-merge: capability scan ran %s on %s.", method_name, model_name)
                    break
                _logger.warning("Auto-merge: capability scan failed %s on %s.", method_name, model_name)

        return merge_success > 0, merge_success, attempts, find_success

    def _detect_dedup_model(self, override_name: str = ""):
        """Detect the deduplication-rule model used by Data Cleaning on this database."""
        if override_name:
            try:
                return self.env[override_name], override_name
            except KeyError:
                _logger.warning("Configured dedup model %s not found; falling back to auto-detection.", override_name)

        candidates = [
            "data.merge.model",
            "data_merge.model",
            "data.cleaning.model",
            "data_cleaning.model",
            "data.merge.rule",
            "data_merge.rule",
        ]
        for name in candidates:
            try:
                m = self.env[name]
                return m, name
            except Exception:
                continue

        from_cron_model, from_cron_name = self._detect_dedup_model_from_cron()
        if from_cron_model:
            return from_cron_model, from_cron_name

        # Try exact model names declared by the data_cleaning/data_merge modules.
        xmlid_models = self._model_names_from_module_xmlids(["data_cleaning", "data_merge"])
        for name in xmlid_models:
            try:
                m = self.env[name]
                fields_map = getattr(m, "_fields", {}) or {}
                if hasattr(m, "_cron_find_duplicates") and ("merge_mode" in fields_map or "active" in fields_map):
                    return m, name
            except Exception:
                continue

        _logger.warning("Auto-merge: no dedup model detected by configured candidates.")
        return None, None

    @api.model
    def _detect_group_model(self, override_name: str = ""):
        """Detect the duplicate group model used by Data Cleaning on this database."""
        if override_name:
            try:
                return self.env[override_name], override_name
            except KeyError:
                _logger.warning("Configured group model %s not found; falling back to auto-detection.", override_name)

        candidates = [
            "data.merge.group",
            "data_merge.group",
            "data_cleaning.duplicate",
            "data.cleaning.duplicate",
            "data_merge.duplicate",
            "data.merge.record",
            "data_merge.record",
            "data.cleaning.record",
            "data_cleaning.record",
            "data.merge.grouping",
            "data_merge.grouping",
        ]
        for name in candidates:
            try:
                m = self.env[name]
                return m, name
            except Exception:
                continue

        # Try exact model names declared by data_cleaning/data_merge module XML IDs.
        xmlid_models = self._model_names_from_module_xmlids(["data_cleaning", "data_merge"])
        for name in xmlid_models:
            try:
                m = self.env[name]
                fields_map = getattr(m, "_fields", {}) or {}
                if getattr(m, "_abstract", False):
                    continue
                if not self._has_merge_capability(m):
                    continue
                if not any(
                    f in fields_map
                    for f in ("model_id", "merge_model_id", "deduplication_model_id", "data_merge_model_id", "dedup_model_id", "rule_id")
                ):
                    continue
                return m, name
            except Exception:
                continue

        _logger.warning("Auto-merge: no group model detected by configured candidates.")
        return None, None

    @api.model
    def _find_group_merge_methods(self, group_model):
        """Return callable method names that can perform merge on a group record."""
        return self._discover_method_names(group_model, mode="merge", include_generic=True)

    @api.model
    def _merge_one_group(self, group_rec):
        """Call the best available merge method for a group record."""
        methods = self._find_group_merge_methods(group_rec)
        for method_name in methods:
            if self._invoke_method_flex(group_rec, method_name):
                return True
        raise AttributeError("No compatible merge method found on group record.")

    @api.model
    def _build_group_search_with_fallback(self, GroupModel, dm, state_value, batch_size):
        domain = self._build_group_domain(GroupModel, dm.id if dm else None, state_value=state_value)
        groups = GroupModel.search(domain, limit=batch_size)
        if not groups and "state" in GroupModel._fields and state_value:
            # Some Odoo versions use different state values than "duplicate".
            fallback_domain = [d for d in domain if d[0] != "state"]
            groups = GroupModel.search(fallback_domain, limit=batch_size)
            if groups:
                _logger.info(
                    "Auto-merge: state '%s' matched no groups; retried without state filter for dedup model id=%s.",
                    state_value,
                    dm.id if dm else "N/A",
                )
        return domain, groups

    @api.model
    def _merge_via_dedup_model(self, dedup_model, batch_size):
        """Fallback: ask dedup rule model itself to execute merge if it exposes a method."""
        def _result_to_count(result):
            if isinstance(result, bool):
                return None
            if isinstance(result, int):
                return max(result, 0)
            if hasattr(result, "__len__"):
                try:
                    return len(result)
                except Exception:
                    return None
            return None

        method_candidates = [
            "_cron_merge_duplicates",
            "action_merge_duplicates",
            "action_process_duplicates",
            "action_merge",
            "merge_duplicates",
        ]
        for method_name in method_candidates:
            if not hasattr(dedup_model, method_name):
                continue
            method = getattr(dedup_model, method_name)
            try:
                result = method()
                return True, method_name, _result_to_count(result)
            except TypeError:
                try:
                    result = method(limit=batch_size)
                    return True, method_name, _result_to_count(result)
                except Exception:
                    _logger.exception("Auto-merge: dedup merge method %s failed (with limit).", method_name)
            except Exception:
                _logger.exception("Auto-merge: dedup merge method %s failed.", method_name)
        return False, None, None

    @api.model
    def _merge_via_dedup_record(self, dm, run_find_first, GroupModel, state_value, batch_size):
        if run_find_first and hasattr(dm, "_cron_find_duplicates"):
            try:
                dm._cron_find_duplicates()
            except Exception:
                _logger.exception("Auto-merge: _cron_find_duplicates failed for dedup model id=%s", dm.id)

        # First try merge methods implemented on dedup rule model itself.
        merged_by_dm, dm_method, merged_hint = self._merge_via_dedup_model(dm, batch_size=batch_size)
        if merged_by_dm:
            merged_count = merged_hint if merged_hint is not None else 1
            _logger.info(
                "Auto-merge: merged using dedup model method %s (id=%s, result_count=%s).",
                dm_method,
                dm.id,
                merged_hint if merged_hint is not None else "unknown",
            )
            return merged_count, 0, True

        # Then fall back to merging duplicate groups.
        domain, groups = self._build_group_search_with_fallback(GroupModel, dm, state_value, batch_size)
        if not groups:
            _logger.info(
                "Auto-merge: no candidate groups for dedup model id=%s (domain=%s).",
                dm.id if dm else "N/A",
                domain,
            )
            return 0, 0, False

        merged = 0
        failed = 0
        for g in groups:
            with self.env.cr.savepoint():
                try:
                    self._merge_one_group(g)
                    merged += 1
                except Exception:
                    failed += 1
                    _logger.exception(
                        "Auto-merge: failed merging group id=%s (dedup_model=%s)",
                        getattr(g, "id", "?"),
                        dm.id if dm else "N/A",
                    )
        return merged, failed, False

    @api.model
    def _build_group_domain(self, group_model, dedup_model_id=None, state_value: str = "duplicate"):
        fields_map = getattr(group_model, "_fields", {}) or {}

        domain = []
        # Link to dedup model record
        if dedup_model_id is not None:
            if "model_id" in fields_map:
                domain.append(("model_id", "=", dedup_model_id))
            elif "merge_model_id" in fields_map:
                domain.append(("merge_model_id", "=", dedup_model_id))
            elif "deduplication_model_id" in fields_map:
                domain.append(("deduplication_model_id", "=", dedup_model_id))
            elif "data_merge_model_id" in fields_map:
                domain.append(("data_merge_model_id", "=", dedup_model_id))
            elif "dedup_model_id" in fields_map:
                domain.append(("dedup_model_id", "=", dedup_model_id))
            elif "rule_id" in fields_map:
                domain.append(("rule_id", "=", dedup_model_id))

        # Only duplicates that are ready
        if "state" in fields_map and state_value:
            domain.append(("state", "=", state_value))

        # Avoid already merged/processed if such flags exist
        if "is_merged" in fields_map:
            domain.append(("is_merged", "=", False))
        if "processed" in fields_map:
            domain.append(("processed", "=", False))

        return domain

    @api.model
    def cron_run(self):
        """Entry point called by ir.cron."""
        _logger.info("Auto-merge runner build: stable-cleanup-v1")
        # --- Config (tweak via System Parameters) ---
        # data_merge_automerge.lock_key (int)
        # data_merge_automerge.batch_size (int)
        # data_merge_automerge.run_find_first (bool)
        # data_merge_automerge.dedup_model (str)
        # data_merge_automerge.group_model (str)
        # data_merge_automerge.target_model (str, default res.partner)
        # data_merge_automerge.only_automatic (bool)
        # data_merge_automerge.state_value (str)
        lock_key = self._get_param_int("data_merge_automerge.lock_key", 987654321)
        batch_size = self._get_param_int("data_merge_automerge.batch_size", 50)
        run_find_first = self._get_param_bool("data_merge_automerge.run_find_first", False)
        dedup_model_override = self._get_param_str("data_merge_automerge.dedup_model", "")
        group_model_override = self._get_param_str("data_merge_automerge.group_model", "")
        only_automatic = self._get_param_bool("data_merge_automerge.only_automatic", True)
        state_value = self._get_param_str("data_merge_automerge.state_value", "duplicate") or "duplicate"

        if batch_size < 1:
            batch_size = 1
        if batch_size > 500:
            batch_size = 500

        if not self._try_advisory_lock(lock_key):
            _logger.info("Auto-merge skipped: another run holds advisory lock (%s).", lock_key)
            return True

        try:
            # Deduplication model (optional for fallback mode)
            DedupModel, dedup_model_name = self._detect_dedup_model(dedup_model_override)
            if DedupModel:
                DedupModel = DedupModel.sudo()
                _logger.info("Auto-merge: using dedup model %s", dedup_model_name)
            else:
                _logger.info(
                    "Auto-merge: could not find deduplication model; merging by duplicate groups only. "
                    "Set data_merge_automerge.dedup_model to improve filtering."
                )

            GroupModel, group_model_name = self._detect_group_model(group_model_override)
            if GroupModel:
                GroupModel = GroupModel.sudo()
                _logger.info("Auto-merge: using group model %s", group_model_name)
            else:
                if DedupModel:
                    GroupModel, group_model_name = self._detect_group_model_from_dedup_model(DedupModel)
                    if GroupModel:
                        GroupModel = GroupModel.sudo()
                        _logger.info("Auto-merge: inferred group model %s from dedup model.", group_model_name)
                if not GroupModel:
                    _logger.info("Auto-merge: could not locate duplicate group model.")

            if not DedupModel and not GroupModel:
                fallback_ok, fallback_merge_success, fallback_attempts, fallback_find_success = self._merge_via_capability_scan(
                    run_find_first=run_find_first,
                    batch_size=batch_size,
                    only_automatic=only_automatic,
                )
                if fallback_ok:
                    _logger.info(
                        "Auto-merge: fallback capability scan completed (merge_success=%s find_success=%s attempts=%s).",
                        fallback_merge_success,
                        fallback_find_success,
                        fallback_attempts,
                    )
                    return True
                _logger.info(
                    "Auto-merge: no usable dedup/group model and fallback scan found no runnable merge methods "
                    "(find_success=%s attempts=%s); aborting run.",
                    fallback_find_success,
                    fallback_attempts,
                )
                return True

            dedup_models = DedupModel.browse() if DedupModel else None
            if DedupModel:
                dm_domain = [("active", "=", True)]
                if only_automatic and "merge_mode" in DedupModel._fields:
                    dm_domain.append(("merge_mode", "=", "automatic"))
                dedup_models = DedupModel.search(dm_domain)
                if not dedup_models:
                    _logger.info("Auto-merge: no matching deduplication models found (domain=%s).", dm_domain)
                    return True

            merged = 0
            failed = 0
            scanned_groups = 0

            loop_items = dedup_models if dedup_models is not None else [None]
            for dm in loop_items:
                if dm:
                    if not GroupModel:
                        if run_find_first and hasattr(dm, "_cron_find_duplicates"):
                            try:
                                dm._cron_find_duplicates()
                            except Exception:
                                _logger.exception("Auto-merge: _cron_find_duplicates failed for dedup model id=%s", dm.id)
                        merged_by_dm, dm_method, merged_hint = self._merge_via_dedup_model(dm, batch_size=batch_size)
                        if merged_by_dm:
                            merged += merged_hint if merged_hint is not None else 1
                            _logger.info(
                                "Auto-merge: merged using dedup model method %s (id=%s, result_count=%s).",
                                dm_method,
                                dm.id,
                                merged_hint if merged_hint is not None else "unknown",
                            )
                        else:
                            _logger.warning(
                                "Auto-merge: dedup model id=%s has no runnable merge method and no group model is available.",
                                dm.id,
                            )
                        continue

                    m_merged, m_failed, merged_by_dm = self._merge_via_dedup_record(
                        dm=dm,
                        run_find_first=run_find_first,
                        GroupModel=GroupModel,
                        state_value=state_value,
                        batch_size=batch_size,
                    )
                    merged += m_merged
                    failed += m_failed
                    if not merged_by_dm:
                        scanned_groups += (m_merged + m_failed)
                    continue

                domain, groups = self._build_group_search_with_fallback(
                    GroupModel=GroupModel,
                    dm=None,
                    state_value=state_value,
                    batch_size=batch_size,
                )
                if not groups:
                    _logger.info(
                        "Auto-merge: no candidate groups for dedup model id=%s (domain=%s).",
                        "N/A",
                        domain,
                    )
                    continue

                scanned_groups += len(groups)
                for g in groups:
                    with self.env.cr.savepoint():
                        try:
                            self._merge_one_group(g)
                            merged += 1
                        except Exception:
                            failed += 1
                            _logger.exception("Auto-merge: failed merging group id=%s (dedup_model=%s)", getattr(g, "id", "?"), "N/A")

            _logger.info(
                "Auto-merge completed: merged=%s failed=%s scanned=%s group_model=%s batch_size=%s",
                merged, failed, scanned_groups, group_model_name, batch_size
            )
            return True

        finally:
            self._advisory_unlock(lock_key)
