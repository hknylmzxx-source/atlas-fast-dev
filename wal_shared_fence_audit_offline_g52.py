"""G52: salt AST denetimi ve temp-dir sentetik WAL laboratuvarı.

Owner import edilmez, kaynak AST'si derlenmez/çalıştırılmaz. Bu modül bir
writer fence uygulaması veya owner güvenlik sertifikası değildir. FakeProcess
aktörleri deterministik bir bellek çizelgesidir; OS süreci/filelock değildir.
Gerçek owner/global lock/mutation lease/WAL üzerinde işlem yoktur. Disk IO
yalnız laboratuvarın kendisinin oluşturduğu TemporaryDirectory içindedir.
CLI yalnız yerleşik sentetik senaryo adlarını kabul eder.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile


ROLES = ("ENTRY", "STOP", "TP1", "TP2", "CANCEL_STOP", "CANCEL_TP1",
         "CANCEL_TP2", "EMERGENCY", "POLICY_RESERVE", "incident")
MARKER_REASON = "WAL_RESOLUTION_MARKER_DISABLED_SHARED_FENCE_UNPROVEN"
ZERO_REASON = "ZERO_ENTRY_AUTO_RESOLUTION_DISABLED_WAL_FENCE_UNPROVEN"
LIMITS = ("STATIC_NOT_CLOSED_WORLD", "DYNAMIC_IMPORTS_UNRESOLVED",
          "MONKEY_PATCH_UNRESOLVED", "WRAPPER_DISPATCH_UNRESOLVED")


@dataclass(frozen=True)
class Decision:
    status: str
    reasons: tuple[str, ...]

    @property
    def unlock(self):
        return False

    @property
    def entry(self):
        return False

    @property
    def resolve(self):
        return False

    def to_dict(self):
        return {"status": self.status, "reasons": list(self.reasons),
                "unlock": False, "entry": False, "resolve": False}


def decision(status, *reasons):
    return Decision(status, tuple(reasons))


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    return "<dynamic>"


def _local_walk(node):
    """İç fonksiyonun çağrısını dış fonksiyon çalıştırmış gibi sayma."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                              ast.Lambda)):
            continue
        yield child
        yield from _local_walk(child)


def _strict_block(node, reason):
    if not isinstance(node, ast.FunctionDef) or node.decorator_list:
        return False
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body.pop(0)
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return False
    call = body[0].exc
    return (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "Blocked" and not call.keywords
            and len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == reason)


def audit_source(source):
    """Yalnız verilen metni AST ile okur; dosya yolu veya import kabul etmez.

    Negatif arama sonucu 'yok/güvenli' kanıtı değildir. Fonksiyon adları,
    lexical çağrı sırası ve with blokları yalnız kaynak işaretleridir.
    """
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    base = {"source_sha256": digest, "evidence_kind": "STATIC_AST_ONLY",
            "runtime_filelock_proven": False, "limitations": list(LIMITS)}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return dict(base, decision=decision("UNPROVEN", "SOURCE_NOT_PARSEABLE").to_dict(),
                    writers=[], calls=[], marker_strict_block_observed=False)
    definitions = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definitions.setdefault(node.name + "." + method.name, []).append(method)
    calls = []
    for name, nodes in definitions.items():
        for node in nodes:
            for call in _local_walk(node):
                if isinstance(call, ast.Call):
                    calls.append({"caller": name, "callee": _dotted(call.func),
                                  "line": call.lineno,
                                  "arguments": [ast.unparse(arg) for arg in call.args]})
    calls.sort(key=lambda row: (row["line"], row["caller"]))
    markers = definitions.get("mark_scope_resolved", [])
    strict = len(markers) == 1 and _strict_block(markers[0], MARKER_REASON)
    specs = (
        ("ENTRY", "one_write", "journal", "role", "venue.acquire_mutation_lease"),
        ("STOP", "one_write", "journal", "role", "venue.acquire_mutation_lease"),
        ("TP1", "one_write", "journal", "role", "venue.acquire_mutation_lease"),
        ("TP2", "one_write", "journal", "role", "venue.acquire_mutation_lease"),
        ("CANCEL_STOP", "cleanup_owned_exits", "journal", "'CANCEL_STOP'", "venue.delete_once"),
        ("CANCEL_TP", "cleanup_owned_exits", "journal", "'CANCEL_TP' + str(index)", "venue.delete_once"),
        ("EMERGENCY", "_emergency_flatten_locked", "journal", "'EMERGENCY'", "venue.post_once"),
        # FAZ2: G54 sonrası doğrudan os.open/os.fsync ortak adapter'a taşındı.
        ("POLICY_RESERVE", "write_policy_reserve_record", "append_reservation", None, "LOG.mkdir"),
        ("incident", "emergency_note", "append_incident", None, "append_incident"),
    )
    writers = []
    for role, caller, sink, first_arg, related in specs:
        local = [c for c in calls if c["caller"] == caller]
        sites = [c for c in local if c["callee"] == sink and (
            first_arg is None or c["arguments"][:1] == [first_arg]) and (
            sink != "os.open" or any("os.O_WRONLY" in arg for arg in c["arguments"][1:2]))]
        signals = [c for c in local if c["callee"] in (
            related, "venue.acquire_mutation_lease", "venue.release_mutation_lease")]
        writers.append({"role": role, "caller": caller, "wal_sites": sites,
                        "related_call_signals": signals,
                        "role_binding": "DYNAMIC_ROLE_REQUIRES_CALLER_REVIEW" if caller == "one_write"
                        else "LOCAL_SYNTAX_ONLY",
                        "decision": decision("UNPROVEN", "SHARED_WRITER_PARTICIPATION_UNPROVEN",
                                             *LIMITS).to_dict()})
    # Kaynakta statik import bile runtime bağlamayı, wrapper'ı veya override'ı kanıtlamaz.
    imports = []
    dynamic_signals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append({"module": node.module, "line": node.lineno})
        elif isinstance(node, ast.Import):
            imports.extend({"module": alias.name, "line": node.lineno} for alias in node.names)
        elif isinstance(node, ast.Call) and _dotted(node.func).split(".")[-1] in (
                "__import__", "import_module", "getattr", "setattr", "exec", "eval"):
            dynamic_signals.append({"callee": _dotted(node.func), "line": node.lineno})
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, (ast.Attribute, ast.Subscript)) for t in targets):
                dynamic_signals.append({"callee": "POSSIBLE_REBINDING", "line": node.lineno})
    zero_nodes = definitions.get("_resolve_verified_zero_entry_scope", [])
    zero_terminal_block = (len(zero_nodes) == 1 and bool(zero_nodes[0].body)
                           and isinstance(zero_nodes[0].body[-1], ast.Raise)
                           and _strict_block(ast.FunctionDef(
                               name="synthetic", args=ast.arguments(posonlyargs=[], args=[],
                               kwonlyargs=[], kw_defaults=[], defaults=[]),
                               body=[zero_nodes[0].body[-1]], decorator_list=[]), ZERO_REASON))
    distinctions = [
        {"kind": "GLOBAL_OWNER_LOCK", "signals": [c for c in calls if
         c["caller"] in ("main", "global_one_trade_lock") and c["callee"] in
         ("global_one_trade_lock", "fcntl.flock")],
         "meaning": "Owner giriş ömrü; tüm WAL yazarlarının ortak fence kanıtı değil."},
        {"kind": "MUTATION_LEASE", "signals": [c for c in calls if c["caller"] in
         ("SignedVenue.acquire_mutation_lease", "SignedVenue._mutation_once")],
         "meaning": "Dış modül/venue dispatch; journal öncesi ortak kapsam ayrıca kanıtlanmalı."},
        {"kind": "SHARED_WRITER_FENCE", "signals": [],
         "meaning": "Tüm writer + resolver için ortak otorite, nesil ve linearization UNPROVEN."},
    ]
    return dict(base, calls=calls, imports=imports, dynamic_signals=dynamic_signals,
                writers=writers, distinctions=distinctions,
                duplicate_definitions=sorted(k for k, v in definitions.items() if len(v) > 1),
                marker_strict_block_observed=strict,
                zero_entry_terminal_block_observed=bool(zero_terminal_block),
                marker_callers=[c for c in calls if c["callee"] == "mark_scope_resolved"],
                decision=decision("UNPROVEN" if strict else "FAIL",
                                  "STRICT_BLOCKED_BARRIER_OBSERVED" if strict else
                                  "RESOLUTION_BARRIER_NOT_STRICT", *LIMITS).to_dict())


# Her satır ayrı kabul koşuludur; true yalnız sentetik caller beyanıdır.
CRITERIA = (
    ("all_writers", "ENTRY/STOP/TP/cancel/emergency/reserve/incident aynı fence'e katılır."),
    ("one_authority", "Aynı lock adı veya owner global lock ortak writer otoritesi sayılmaz."),
    ("atomic_mutation", "Son kontrol ile yazım arasında geç yazar penceresi kalmaz."),
    ("durable_epoch", "Crash/restart sonrası kalıcı epoch eski token'ı reddeder."),
    ("no_aba", "Generation/holder/nonce yeniden kullanılamaz; aynı görünüm yeterli değildir."),
    ("resolver_quiescence", "Resolver snapshot ve olası marker sınırında tüm yazarlar dışlanır."),
    ("lock_order", "Owner lock, lease ve fence sırası/deadlock ayrı doğrulanır."),
    ("file_and_directory_fsync", "Dosya fsync ve dizin fsync/ACK belirsizliği ayrılır."),
    ("anchored_replay", "Eksik/torn/duplicate/reordered kayıt ve suffix kaybı dış kökle denetlenir."),
    ("unknown_no_retry", "UNKNOWN, retry/resolve veya yeni giriş yetkisi üretmez."),
)


def acceptance_matrix(claims):
    valid = (type(claims) is dict and set(claims) == {key for key, _ in CRITERIA}
             and all(value is None or type(value) is bool for value in claims.values()))
    rows = []
    for key, requirement in CRITERIA:
        value = claims.get(key) if valid else None
        status = "FAIL" if not valid or value is False else (
            "PASS_SYNTHETIC_ONLY" if value is True else "UNPROVEN")
        rows.append({"criterion": key, "requirement": requirement,
                     "decision": decision(status, "MOCK_CLAIM_ONLY" if valid else
                                          "INVALID_CLAIM_SCHEMA").to_dict()})
    status = "FAIL" if any(r["decision"]["status"] == "FAIL" for r in rows) else (
        "UNPROVEN" if any(r["decision"]["status"] == "UNPROVEN" for r in rows)
        else "PASS_SYNTHETIC_ONLY")
    return {"evidence_kind": "SYNTHETIC_CLAIMS_ONLY", "rows": rows,
            "decision": decision(status, "OWNER_SAFETY_NOT_PROVEN").to_dict()}


@dataclass(frozen=True)
class FakeProcess:
    name: str


@dataclass(frozen=True)
class MockToken:
    authority: str
    epoch: int
    generation: int
    actor: str
    role: str


def _hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class SyntheticLab:
    """İki fake-process, tek bellek otoritesi; gerçek mutex/lease yoktur.

    commit içindeki son check + dosya mutation'ı tek sıralı model varsayımıdır.
    OS concurrency, kalıcı epoch ve güç kesintisi dayanıklılığı KANITLAMAZ.
    Crash noktaları hata enjeksiyonudur; gerçek process kill değildir.
    """
    def __init__(self):
        self._temp = tempfile.TemporaryDirectory(prefix="g52-synthetic-")
        self.root = Path(self._temp.name)
        self._authority = "MOCK_SHARED:" + self.root.name
        self.actors = (FakeProcess("fake-A"), FakeProcess("fake-B"))
        self.epoch = 1
        self.generation = 0
        self.held = None
        self.count = 0
        self.tail = "0" * 64
        self.events = []
        self._attempted = set()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self._temp.cleanup()

    def acquire(self, actor, role, authority="MOCK_SHARED"):
        if (type(actor) is not FakeProcess or actor not in self.actors
                or role not in (*ROLES, "RESOLVER") or authority != "MOCK_SHARED"):
            return None, decision("FAIL", "WRONG_ACTOR_ROLE_OR_AUTHORITY")
        if self.held is not None:
            return None, decision("FAIL", "MOCK_CONTENTION")
        self.generation += 1
        self.held = MockToken(self._authority, self.epoch, self.generation, actor.name, role)
        return self.held, decision("PASS_SYNTHETIC_ONLY", "MOCK_GRANT_ONLY")

    def current(self, token):
        return (type(token) is MockToken
                and type(token.epoch) is int and type(token.generation) is int
                and type(token.authority) is str and type(token.actor) is str
                and type(token.role) is str and token.authority == self._authority
                and self.held is not None and token == self.held
                and token.epoch == self.epoch and token.generation == self.generation)

    def release(self, token):
        if not self.current(token):
            return decision("FAIL", "STALE_TOKEN_RELEASE")
        self.held = None
        return decision("PASS_SYNTHETIC_ONLY", "MOCK_RELEASE_ONLY")

    def restart(self):
        # Kalıcı sayaç değildir; yalnız deterministik ABA karşı örneği.
        self.epoch += 1
        self.generation += 1
        self.held = None
        return decision("UNPROVEN", "MODEL_RESTART_REPLAY_ONLY_NO_DURABLE_EPOCH")

    def snapshot(self):
        return tuple((p.name, hashlib.sha256(p.read_bytes()).hexdigest())
                     for p in sorted(self.root.glob("*.json")))

    def compare_snapshots(self, before, after):
        return decision("UNPROVEN" if before == after else "FAIL",
                        "EQUAL_READS_DO_NOT_EXCLUDE_LATE_WRITER_OR_ABA" if before == after
                        else "WAL_CHANGED_KEEP_BLOCKED")

    def resolve_request(self):
        # Bilerek marker yazımı yoktur; olumlu mock grant dahi açamaz.
        return decision("UNPROVEN", "SHARED_OWNER_WRITER_FENCE_UNPROVEN_NO_MARKER")

    def commit(self, token, role, *, crash=None, fsync_error=None):
        if crash not in (None, "before_append", "torn", "after_write", "after_file_fsync",
                         "after_directory_fsync", "before_ack") or fsync_error not in (None, "file", "directory"):
            return decision("FAIL", "INVALID_FAULT_POINT")
        if not self.current(token) or role not in ROLES or token.role != role:
            return decision("FAIL", "STALE_OR_WRONG_ROLE_NO_APPEND")
        if token in self._attempted:
            return decision("FAIL", "MOCK_GRANT_ALREADY_ATTEMPTED_NO_RETRY")
        self._attempted.add(token)
        if crash == "before_append":
            return decision("UNPROVEN", "CRASH_BEFORE_APPEND_REPLAY_ONLY")
        return self._append(role, token.actor, crash, fsync_error)

    def rogue_append(self, role):
        """Yalnız mock nonparticipant; karşı örneği üretmek için fence'i atlar."""
        if role not in ROLES:
            return decision("FAIL", "UNKNOWN_MOCK_ROLE")
        return self._append(role, "fake-B", None, None)

    def _append(self, role, actor, crash, fsync_error):
        row = {"schema": "G52_SYNTHETIC_ONLY", "sequence": self.count + 1,
               "previous": self.tail, "epoch": self.epoch, "generation": self.generation,
               "actor": actor, "role": role}
        row["digest"] = _hash(row)
        raw = (json.dumps(row, sort_keys=True) + "\n").encode()
        target = self.root / ("mock_%04d.json" % row["sequence"])
        try:
            with target.open("xb") as stream:
                stream.write(raw[:len(raw)//2] if crash == "torn" else raw)
                stream.flush()
                self.events.append("WRITE")
                if crash in ("torn", "after_write"):
                    return decision("UNPROVEN", "CRASH_BEFORE_FILE_FSYNC_UNKNOWN_NO_RETRY")
                if fsync_error == "file":
                    raise OSError("synthetic file fsync failure")
                os.fsync(stream.fileno())
                self.events.append("FILE_FSYNC")
            if crash == "after_file_fsync":
                return decision("UNPROVEN", "CRASH_BEFORE_DIRECTORY_FSYNC_UNKNOWN_NO_RETRY")
            if fsync_error == "directory":
                raise OSError("synthetic directory fsync failure")
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.events.append("DIRECTORY_FSYNC")
        except FileExistsError:
            return decision("FAIL", "DUPLICATE_MOCK_RECORD_NO_RETRY")
        except OSError:
            return decision("UNPROVEN", "FSYNC_OR_IO_UNKNOWN_NO_RETRY")
        self.count += 1
        self.tail = row["digest"]
        if crash in ("after_directory_fsync", "before_ack"):
            return decision("UNPROVEN", "DURABLE_BYTES_WITHOUT_ACK_UNKNOWN_NO_RETRY")
        self.events.append("ACK")
        return decision("PASS_SYNTHETIC_ONLY", "TEMP_FILE_ACK_ONLY_NOT_OWNER_DURABILITY")

    def replay(self, mock_anchor=None):
        paths = sorted(self.root.iterdir())
        if not paths:
            return decision("UNPROVEN", "EMPTY_STORE_NOT_CLEAN_PROOF")
        previous = "0" * 64
        epoch, generation = 0, 0
        expected_keys = {"schema", "sequence", "previous", "epoch", "generation",
                         "actor", "role", "digest"}
        try:
            for sequence, path in enumerate(paths, 1):
                if path.is_symlink() or path.name != "mock_%04d.json" % sequence or not path.is_file():
                    raise ValueError("unexpected entry")
                raw = path.read_bytes()
                row = json.loads(raw, object_pairs_hook=_unique_object)
                if type(row) is not dict or set(row) != expected_keys or not raw.endswith(b"\n"):
                    raise ValueError("schema or torn record")
                digest = row.pop("digest")
                if (row["schema"] != "G52_SYNTHETIC_ONLY" or type(row["sequence"]) is not int
                        or row["sequence"] != sequence or row["previous"] != previous
                        or row["role"] not in ROLES or row["actor"] not in ("fake-A", "fake-B")
                        or type(row["epoch"]) is not int or row["epoch"] < 1
                        or type(row["generation"]) is not int or row["generation"] < 1
                        or row["epoch"] < epoch or row["generation"] < generation
                        or digest != _hash(row)):
                    raise ValueError("chain or identity")
                previous, epoch, generation = digest, row["epoch"], row["generation"]
        except (OSError, ValueError, TypeError, KeyError):
            return decision("UNPROVEN", "REPLAY_CORRUPT_OR_INCOMPLETE_NO_RETRY")
        if mock_anchor is None:
            return decision("UNPROVEN", "VALID_CHAIN_WITHOUT_EXTERNAL_ANCHOR_SUFFIX_LOSS_UNPROVEN")
        if (type(mock_anchor) is not tuple or len(mock_anchor) != 2
                or type(mock_anchor[0]) is not int or type(mock_anchor[1]) is not str
                or mock_anchor != (len(paths), previous)):
            return decision("FAIL", "MOCK_ANCHOR_MISMATCH")
        return decision("PASS_SYNTHETIC_ONLY", "MOCK_ANCHOR_NOT_EXTERNAL_TRUST_ROOT_REPLAY_ONLY")


SCENARIOS = ("shared_mock", "late_writer", "nonparticipant_incident", "aba", "crash")


def run_scenario(name):
    if name not in SCENARIOS:
        return {"decision": decision("FAIL", "UNKNOWN_SYNTHETIC_SCENARIO").to_dict()}
    with SyntheticLab() as lab:
        a, b = lab.actors
        token, grant = lab.acquire(a, "ENTRY")
        results = [grant]
        if name == "shared_mock":
            _, busy = lab.acquire(b, "incident")
            results.extend((busy, lab.commit(token, "ENTRY"), lab.release(token)))
            other, _ = lab.acquire(b, "incident")
            results.append(lab.commit(other, "incident"))
        elif name == "late_writer":
            assert lab.current(token)
            lab.release(token)
            lab.acquire(b, "STOP")
            results.append(lab.commit(token, "ENTRY"))
        elif name == "nonparticipant_incident":
            before = lab.snapshot()
            results.append(lab.compare_snapshots(before, lab.snapshot()))
            results.append(lab.rogue_append("incident"))
            results.append(lab.compare_snapshots(before, lab.snapshot()))
        elif name == "aba":
            before = lab.snapshot()
            results.append(lab.restart())
            lab.acquire(a, "ENTRY")
            results.append(lab.compare_snapshots(before, lab.snapshot()))
            results.append(lab.commit(token, "ENTRY"))
        else:
            results.append(lab.commit(token, "ENTRY", crash="after_write"))
        results.append(lab.replay((lab.count, lab.tail)))
        return {"scenario": name, "evidence_kind": "TEMP_DIR_FAKE_PROCESS_ONLY",
                "os_process_concurrency_tested": False, "decisions": [r.to_dict() for r in results],
                "decision": lab.resolve_request().to_dict()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="G52: yalnız yerleşik sentetik fixture; yetki üretmez.")
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), default="all")
    args = parser.parse_args(argv)
    names = SCENARIOS if args.scenario == "all" else (args.scenario,)
    report = {"scenarios": [run_scenario(name) for name in names],
              "matrix": acceptance_matrix({key: None for key, _ in CRITERIA}),
              "decision": decision("UNPROVEN", "NO_OWNER_IMPORT_NO_SHARED_FENCE_PROOF").to_dict()}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0  # Rapor üretildi; güvenlik/entry/resolve başarısı değildir.


if __name__ == "__main__":
    raise SystemExit(main())
