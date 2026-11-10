"""F-00/B -- Izolasyon kanit testi (yalnizca standart kutuphane).

Bu test, depodaki TUM test paketinin canli sisteme dokunmadigini kanitlar.

Yontem:
  * Test paketi ayri bir Python surecinde, yorumlayici acilisindan itibaren
    etkin olan bir izolasyon korumasiyla (``sitecustomize`` + PEP 578 audit
    hook) bastan sona calistirilir. Testlerin baslattigi alt Python surecleri
    de ``PYTHONPATH`` mirasiyla ayni korumayi alir.
  * Koruma su ihlalleri hem ENGELLER hem KAYDEDER:
      - her ag baglantisi / DNS cozumlemesi (socket.connect, getaddrinfo ...)
      - ``/Users/Shared/Atlas/`` ve ``~/Atlas/AKTIF_*`` yollarina dosya erisimi
      - Owner SDK / canli modullerin import edilmesi (liste kaynaktan cikarilir)
    ve sunu KAYDEDER (okumayi bozmadan):
      - BINANCE / API_KEY / SECRET iceren ortam degiskeni okumalari
        (yalnizca degisken ADI kaydedilir; deger asla kaydedilmez).
  * Ayni paket bir kez de korumasiz calistirilir; iki calismanin test sayisi
    ve basarisiz test kumesi karsilastirilarak korumanin mevcut testlerin
    davranisini degistirmedigi gosterilir.

Koruma ana test surecine KURULMAZ (audit hook geri alinamaz); boylece mevcut
testlerin normal calismasi hicbir sekilde etkilenmez.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
THIS = Path(__file__).resolve()

CHILD_FLAG = "ATLAS_ISOLATION_GUARD_CHILD"
IN_CHILD = CHILD_FLAG in os.environ
SKIP_REASON = "izolasyon calismasinin icindeki ic ice cagri (ozyineleme engeli)"

# Herkese acik ucuncu taraf paketler: import edilmeleri tek basina canli islem
# degildir, ancak kaydedilir ve dosya konumlari yasak bolgede olmamalidir.
THIRD_PARTY = frozenset({
    "requests",
    "binance_common",
    "binance_sdk_derivatives_trading_usds_futures",
})
# AGENTS.md madde 3'te adi gecen canli modul (bu depoda import edilmiyor).
DOC_LIVE = frozenset({"fast_signal_live_executor"})
# owner_single_execution.py icinden tespit edilen canli Owner SDK modulleri.
# Kaynakta yeni bir canli import belirirse test_live_module_list_matches_source
# kirmizi olur ve liste bilincli olarak guncellenmelidir.
EXPECTED_SOURCE_LIVE = frozenset({
    "fast_r8_offline_integration_contract_F3F4_CANDIDATE",
    "observer_liveness",
    "owner_mutation_fence",
    "live_pilot",
    "sdk_signed_sender",
    "exact_trade_approval",
    "r2_guard_venue",
})

VIOLATION_KINDS = frozenset({
    "network", "path", "command", "import_blocked", "env_read",
    "sys_path", "module_file",
})


def _repo_py_files():
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT)
        if rel.parts and rel.parts[0].startswith("."):
            continue
        yield p


def derive_live_modules():
    """Kaynaktaki yerel olmayan, stdlib disi ve ucuncu taraf olmayan importlar."""
    local = {p.stem for p in _repo_py_files()}
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    found = set()
    for p in _repo_py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"), str(p))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                names = [n.module]
            else:
                continue
            for name in names:
                top = name.partition(".")[0]
                if top not in stdlib and top not in local and top not in THIRD_PARTY:
                    found.add(top)
    return found


LIVE_MODULES = frozenset(derive_live_modules() | EXPECTED_SOURCE_LIVE | DOC_LIVE)


# --------------------------------------------------------------------------
# Alt sureclere sitecustomize.py olarak yazilan koruma kodu.
# Yalnizca os, sys, json, _thread, atexit kullanir (yan etkiyi en aza indirmek).
# --------------------------------------------------------------------------
GUARD_SOURCE = r'''
# ATLAS izolasyon korumasi -- tests/test_isolation_guard.py tarafindan uretilir.
import os, sys, json, _thread, atexit

def _install():
    report_dir = os.environ.get("ATLAS_ISOLATION_REPORT_DIR")
    if not report_dir:
        return
    repo = os.environ.get("ATLAS_ISOLATION_REPO", "")
    live = frozenset(x for x in os.environ.get("ATLAS_ISOLATION_LIVE_MODULES", "").split(",") if x)
    third = frozenset(x for x in os.environ.get("ATLAS_ISOLATION_THIRD_PARTY", "").split(",") if x)
    report = os.path.join(report_dir, "proc-%d.jsonl" % os.getpid())
    me = os.path.abspath(__file__)
    busy = set()

    def _cf(s):
        s = s.casefold()
        if s.startswith("/system/volumes/data/"):
            s = s[len("/system/volumes/data"):]
        return s.rstrip("/") or "/"

    roots = ["/users/shared/atlas"]
    homes = [_cf(os.path.expanduser("~/Atlas"))]
    for x in os.environ.get("ATLAS_ISOLATION_EXTRA_ROOTS", "").split(os.pathsep):
        if x:
            roots.append(_cf(os.path.abspath(x)))
    for x in os.environ.get("ATLAS_ISOLATION_EXTRA_HOMES", "").split(os.pathsep):
        if x:
            homes.append(_cf(os.path.abspath(x)))

    def _where():
        out = []
        try:
            f = sys._getframe(2)
        except Exception:
            return out
        while f is not None and len(out) < 4:
            fn = f.f_code.co_filename
            if repo and fn.startswith(repo) and fn != me:
                out.append("%s:%d" % (os.path.relpath(fn, repo), f.f_lineno))
            f = f.f_back
        return out

    def rec(kind, **kw):
        tid = _thread.get_ident()
        if tid in busy:
            return
        busy.add(tid)
        try:
            kw["kind"] = kind
            kw["pid"] = os.getpid()
            kw["where"] = _where()
            line = json.dumps(kw, default=repr, ensure_ascii=True)
            with open(report, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        finally:
            busy.discard(tid)

    def forbidden_path(p):
        if p is None or isinstance(p, int):
            return None
        try:
            s = os.fsdecode(p)
        except Exception:
            return None
        if not s:
            return None
        try:
            a = s if os.path.isabs(s) else os.path.join(os.getcwd(), s)
        except Exception:
            a = s
        cands = {os.path.normpath(a)}
        try:
            cands.add(os.path.realpath(a))
        except Exception:
            pass
        for c in cands:
            cf = _cf(c)
            for r in roots:
                if cf == r or cf.startswith(r + "/"):
                    return c
            for h in homes:
                if cf.startswith(h + "/"):
                    if cf[len(h) + 1:].split("/", 1)[0].startswith("aktif_"):
                        return c
        return None

    def forbidden_text(t):
        try:
            s = os.fsdecode(t) if not isinstance(t, str) else t
        except Exception:
            return False
        cf = s.casefold()
        if "atlas/aktif_" in cf:
            return True
        return any(r in cf for r in roots) or any((h + "/aktif_") in cf for h in homes)

    NET = {"socket.connect", "socket.sendto", "socket.sendmsg",
           "socket.getaddrinfo", "socket.gethostbyname",
           "socket.gethostbyname_ex", "socket.gethostbyaddr",
           "socket.getnameinfo"}
    PATHS = {
        "open": (0,), "os.listdir": (0,), "os.scandir": (0,), "os.chdir": (0,),
        "os.mkdir": (0,), "os.remove": (0,), "os.rmdir": (0,),
        "os.rename": (0, 1), "os.symlink": (0, 1), "os.link": (0, 1),
        "os.truncate": (0,), "os.chmod": (0,), "os.chown": (0,),
        "os.utime": (0,), "os.chflags": (0,), "os.listxattr": (0,),
        "os.getxattr": (0,), "os.setxattr": (0,), "os.removexattr": (0,),
        "shutil.copyfile": (0, 1), "shutil.copytree": (0, 1),
        "shutil.rmtree": (0,), "shutil.move": (0, 1), "shutil.copymode": (0, 1),
        "shutil.copystat": (0, 1), "shutil.chown": (0,), "glob.glob": (0,),
        "glob.glob/2": (0,), "sqlite3.connect": (0,), "os.exec": (0,),
        "os.posix_spawn": (0,), "pathlib.Path.glob": (0,),
        "pathlib.Path.rglob": (0,),
    }

    def hook(event, args):
        if _thread.get_ident() in busy:
            return
        if event in NET:
            rec("network", event=event, target=repr(args[1:] if event.startswith("socket.s") or event == "socket.connect" else args)[:300])
            raise PermissionError("ISOLATION_GUARD: ag erisimi engellendi (%s)" % event)
        idx = PATHS.get(event)
        if idx:
            for i in idx:
                if i < len(args):
                    hit = forbidden_path(args[i])
                    if hit:
                        rec("path", event=event, path=hit)
                        raise PermissionError("ISOLATION_GUARD: yasak yol engellendi: %s" % hit)
        if event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn"):
            if event == "os.system":
                parts = [args[0]]
            elif event == "os.spawn":
                parts = [args[1]] + list(args[2] or [])
            else:
                a1 = args[1] if len(args) > 1 else None
                parts = [args[0]] + (list(a1) if isinstance(a1, (list, tuple)) else [a1])
            if event == "subprocess.Popen" and len(args) > 2 and forbidden_path(args[2]):
                rec("path", event=event, path=str(args[2]))
                raise PermissionError("ISOLATION_GUARD: yasak calisma dizini")
            for part in parts:
                if part is not None and forbidden_text(part):
                    rec("command", event=event, part=str(part)[:300])
                    raise PermissionError("ISOLATION_GUARD: yasak yol iceren komut")
            if event == "subprocess.Popen":
                argv = [os.fsdecode(x) if isinstance(x, bytes) else str(x)
                        for x in parts[1:] if x is not None]
                exe = os.fsdecode(args[0]) if args[0] is not None else (argv[0] if argv else "")
                is_py = exe == sys.executable or "python" in os.path.basename(exe).casefold()
                env = args[3] if len(args) > 3 else None
                rec("spawn", python=bool(is_py), argv=[x[:80] for x in argv[:4]],
                    custom_env=env is not None)
                flags = {"-I", "-E", "-S"}
                if is_py and (flags & set(argv) or env is not None):
                    rec("uncovered_child", argv=[x[:80] for x in argv[:4]])

    class LiveModuleFinder:
        @classmethod
        def find_spec(cls, fullname, path=None, target=None):
            top = fullname.partition(".")[0]
            if top in live:
                rec("import_blocked", module=fullname)
                raise ModuleNotFoundError(
                    "ISOLATION_GUARD: canli modul importu engellendi: %s" % fullname,
                    name=fullname)
            if top in third and fullname == top:
                rec("third_party_import", module=fullname)
            return None

    SENSITIVE = ("BINANCE", "API_KEY", "APIKEY", "SECRET")

    class GuardedEnviron(type(os.environ)):
        def __getitem__(self, key):
            try:
                k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
                up = k.upper()
                if any(s in up for s in SENSITIVE):
                    f = sys._getframe(1)
                    bulk = (f.f_code.co_name == "__iter__"
                            and f.f_code.co_filename.endswith("_collections_abc.py")) or \
                           (f.f_code.co_name == "copy" and f.f_code.co_filename.endswith("os.py"))
                    rec("env_read", key=k, mode="bulk" if bulk else "direct")
            except Exception:
                pass
            return super().__getitem__(key)

    rec("start", argv=[str(x)[:80] for x in sys.argv[:3]], executable=sys.executable)
    for entry in list(sys.path):
        if forbidden_path(entry):
            rec("sys_path", entry=entry)
    os.environ.__class__ = GuardedEnviron
    if hasattr(os, "environb"):
        os.environb.__class__ = GuardedEnviron
    sys.meta_path.insert(0, LiveModuleFinder)
    sys.addaudithook(hook)

    def final():
        for entry in list(sys.path):
            if forbidden_path(entry):
                rec("sys_path", entry=entry)
        for name, mod in list(sys.modules.items()):
            fn = getattr(mod, "__file__", None)
            if fn and forbidden_path(fn):
                rec("module_file", module=name, file=fn)
            top = name.partition(".")[0]
            if top in live:
                rec("module_stub", module=name, file=fn)
            if top in third and name == top:
                rec("third_party_loaded", module=name, file=fn)
        rec("exit")
    atexit.register(final)

_install()
del _install
'''


def _guard_env(base_dir: Path, report_dir: Path, extra_roots=(), extra_homes=()):
    site_dir = base_dir / "guard_site"
    site_dir.mkdir()
    (site_dir / "sitecustomize.py").write_text(GUARD_SOURCE, encoding="utf-8")
    env = dict(os.environ)
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(site_dir) + (os.pathsep + old if old else "")
    env["ATLAS_ISOLATION_REPORT_DIR"] = str(report_dir)
    env["ATLAS_ISOLATION_REPO"] = str(ROOT)
    env["ATLAS_ISOLATION_LIVE_MODULES"] = ",".join(sorted(LIVE_MODULES))
    env["ATLAS_ISOLATION_THIRD_PARTY"] = ",".join(sorted(THIRD_PARTY))
    env["ATLAS_ISOLATION_EXTRA_ROOTS"] = os.pathsep.join(map(str, extra_roots))
    env["ATLAS_ISOLATION_EXTRA_HOMES"] = os.pathsep.join(map(str, extra_homes))
    env[CHILD_FLAG] = "1"
    return env


def _read_records(report_dir: Path):
    out = []
    for p in sorted(report_dir.glob("proc-*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


_FAIL_RE = re.compile(r"^(ERROR|FAIL): (.+)$", re.M)
_RAN_RE = re.compile(r"^Ran (\d+) tests?", re.M)


def _summary(output: str):
    ran = _RAN_RE.search(output)
    return (int(ran.group(1)) if ran else None,
            frozenset(m.group(0) for m in _FAIL_RE.finditer(output)))


def _run_suite(env):
    return subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(TESTS)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=600)


def _fmt(records):
    return "\n".join("  - " + json.dumps(r, ensure_ascii=False) for r in records)


@unittest.skipIf(IN_CHILD, SKIP_REASON)
class FullSuiteIsolation(unittest.TestCase):
    """Tum test paketi koruma altinda calistirilir ve ihlaller denetlenir."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="atlas_isolation_")
        base = Path(cls._tmp.name)
        report = base / "report"
        report.mkdir()
        base_env = dict(os.environ)
        base_env[CHILD_FLAG] = "1"
        cls.baseline = _run_suite(base_env)
        cls.guarded = _run_suite(_guard_env(base, report))
        cls.records = _read_records(report)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _kind(self, *kinds):
        return [r for r in self.records if r["kind"] in kinds]

    def _out(self):
        return self.guarded.stdout[-4000:] + self.guarded.stderr[-4000:]

    def test_guard_was_active_and_suite_ran(self):
        starts = self._kind("start")
        self.assertTrue(starts, "koruma hicbir surecte baslamadi:\n" + self._out())
        ran, _ = _summary(self.guarded.stderr)
        self.assertTrue(ran and ran > 0, self._out())

    def test_no_network_connection(self):
        hits = self._kind("network")
        self.assertEqual(hits, [], "ag baglantisi denemesi tespit edildi:\n" + _fmt(hits))

    def test_no_forbidden_path_access(self):
        hits = self._kind("path", "command", "sys_path", "module_file")
        self.assertEqual(hits, [], "yasak yol erisimi tespit edildi:\n" + _fmt(hits))

    def test_no_live_module_import(self):
        hits = self._kind("import_blocked")
        self.assertEqual(hits, [], "canli modul importu tespit edildi:\n" + _fmt(hits))

    def test_no_sensitive_env_read(self):
        hits = self._kind("env_read")
        self.assertEqual(hits, [], "hassas ortam degiskeni okundu:\n" + _fmt(hits))

    def test_all_python_children_were_guarded(self):
        uncovered = self._kind("uncovered_child")
        self.assertEqual(uncovered, [], "korumasiz alt surec:\n" + _fmt(uncovered))
        py_spawns = [r for r in self._kind("spawn") if r.get("python")]
        starts = self._kind("start")
        # 1 = paketi calistiran ana alt surec
        self.assertGreaterEqual(len(starts), 1 + len(py_spawns),
                                "baslatilan her Python alt sureci korumayi almadi")

    def test_guard_does_not_change_suite_outcome(self):
        self.assertEqual(_summary(self.baseline.stderr), _summary(self.guarded.stderr),
                         "koruma testlerin sonucunu degistirdi:\n" + self._out())
        self.assertEqual(self.baseline.returncode, self.guarded.returncode)

    def test_no_violation_of_any_kind(self):
        hits = [r for r in self.records if r["kind"] in VIOLATION_KINDS]
        self.assertEqual(hits, [], "izolasyon ihlali:\n" + _fmt(hits))


@unittest.skipIf(IN_CHILD, SKIP_REASON)
class StaticIsolation(unittest.TestCase):
    """Kaynak koddan statik kontroller (calisma zamani denetimini tamamlar)."""

    def test_live_module_list_matches_source(self):
        derived = derive_live_modules()
        self.assertEqual(derived, set(EXPECTED_SOURCE_LIVE),
                         "kaynakta beklenmeyen canli import kumesi; listeyi gozden gecirin")

    def test_no_forbidden_path_literal_in_source(self):
        pat = re.compile(r"/Users/Shared/Atlas|Atlas/AKTIF_|AKTIF_OWNER", re.I)
        hits = []
        for p in _repo_py_files():
            if p.resolve() == THIS:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"), str(p))
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and pat.search(n.value):
                    hits.append("%s:%d" % (p.relative_to(ROOT), n.lineno))
        self.assertEqual(hits, [], "kaynakta yasak yol sabiti bulundu")


PROBE = r'''
import json, os, socket, subprocess, sys
fake_root, fake_home = sys.argv[1], sys.argv[2]
res = {}
s = socket.socket()
try:
    s.connect(("127.0.0.1", 9)); res["net"] = "connected"
except PermissionError as e:
    res["net"] = "blocked" if "ISOLATION_GUARD" in str(e) else "perm"
except OSError:
    res["net"] = "not_blocked"
finally:
    s.close()
for name, p in [("shared", os.path.join(fake_root, "x.json")),
                ("aktif", os.path.join(fake_home, "AKTIF_OWNER_SDK", "cfg.json")),
                ("aktif_case", os.path.join(fake_home, "aktif_owner_bridge", "x")),
                ("real_shared", "/Users/Shared/Atlas/__isolation_probe_never_exists__")]:
    try:
        open(p).close(); res[name] = "opened"
    except PermissionError as e:
        res[name] = "blocked" if "ISOLATION_GUARD" in str(e) else "perm"
    except OSError:
        res[name] = "not_blocked"
try:
    import sdk_signed_sender; res["imp"] = "imported"
except ModuleNotFoundError as e:
    res["imp"] = "blocked" if "ISOLATION_GUARD" in str(e) else "missing"
os.environ.get("BINANCE_API_KEY"); os.getenv("MY_SECRET_TOKEN"); "BINANCE_X" in os.environ
os.environ.get("HOME")
os.environ["ATLAS_ISOLATION_PROBE_VAR"] = "1"
res["env_ok"] = os.environ.get("ATLAS_ISOLATION_PROBE_VAR") == "1"
code = ("import socket\ntry:\n socket.getaddrinfo('localhost', 9)\n print('leak')\n"
        "except PermissionError:\n print('blocked')")
res["grandchild"] = subprocess.run([sys.executable, "-B", "-c", code],
                                   capture_output=True, text=True).stdout.strip()
with open(__file__) as fh:
    res["allowed"] = bool(fh.read(1))
print(json.dumps(res))
'''


@unittest.skipIf(IN_CHILD, SKIP_REASON)
class GuardSelfTest(unittest.TestCase):
    """Korumanin gercekten engelledigini/kaydettigini sahte yollarla dogrular.

    Gercek yasak yol yalnizca acilmasi engellenen, var olmayan bir dosya adiyla
    denenir; koruma calismiyorsa bile o dosya mevcut olmadigindan hicbir canli
    veri okunmaz. Ag denemesi yerel ve zararsizdir (127.0.0.1:9, localhost).
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="atlas_isolation_self_")
        base = Path(cls._tmp.name)
        report = base / "report"
        report.mkdir()
        fake_root = base / "fake_shared_atlas"
        fake_home = base / "fake_home_atlas"
        probe = base / "probe.py"
        probe.write_text(PROBE, encoding="utf-8")
        env = _guard_env(base, report, [fake_root], [fake_home])
        cls.proc = subprocess.run(
            [sys.executable, "-B", str(probe), str(fake_root), str(fake_home)],
            cwd=str(base), env=env, capture_output=True, text=True, timeout=120)
        lines = cls.proc.stdout.strip().splitlines()
        cls.res = json.loads(lines[-1]) if lines else {}
        cls.records = _read_records(report)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_probe_ran(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_network_blocked_and_recorded(self):
        self.assertEqual(self.res.get("net"), "blocked")
        self.assertEqual(self.res.get("grandchild"), "blocked")
        pids = {r["pid"] for r in self.records if r["kind"] == "network"}
        self.assertGreaterEqual(len(pids), 2, "alt-alt surec korunmadi")

    def test_forbidden_paths_blocked_and_recorded(self):
        for k in ("shared", "aktif", "aktif_case", "real_shared"):
            self.assertEqual(self.res.get(k), "blocked", k)
        self.assertEqual(len([r for r in self.records if r["kind"] == "path"]), 4)
        self.assertTrue(self.res.get("allowed"))

    def test_live_import_blocked_and_recorded(self):
        self.assertEqual(self.res.get("imp"), "blocked")
        mods = [r["module"] for r in self.records if r["kind"] == "import_blocked"]
        self.assertEqual(mods, ["sdk_signed_sender"])

    def test_sensitive_env_reads_recorded_without_values(self):
        reads = [r for r in self.records if r["kind"] == "env_read"]
        self.assertEqual({r["key"] for r in reads},
                         {"BINANCE_API_KEY", "MY_SECRET_TOKEN", "BINANCE_X"})
        self.assertTrue(all(set(r) <= {"kind", "pid", "where", "key", "mode"} for r in reads))
        self.assertTrue(self.res.get("env_ok"))


if __name__ == "__main__":
    unittest.main()
