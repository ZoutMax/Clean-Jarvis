"""Offline test harness for the Jarvis Home patch kit. Plain unittest, no live Home Assistant, no network calls
into the home: a fake hass records what JARVIS tries to do.

Run:  JARVIS_VERSION=<tag> JARVIS_BUILT=<built custom_components/jarvis> python -m pytest patches/tests

The tests need a freshly patched JARVIS. Set JARVIS_BUILT to the custom_components\\jarvis folder of a patched
copy, or leave it out: the upstream release is then downloaded once to the temp folder and patched there.
Only temporary folders are ever written.
"""
import asyncio, importlib, importlib.abc, importlib.machinery, io, os, shutil, sys, tempfile, types, urllib.request, zipfile
from unittest import mock

VERSION = os.environ.get("JARVIS_VERSION", "9.86.0")
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://github.com/sam3gp8/jarvis-aio/archive/refs/tags/%s.zip" % VERSION


def upstream_zip():
    path = os.path.join(tempfile.gettempdir(), "jarvis-aio-%s.zip" % VERSION)
    if not os.path.exists(path):
        req = urllib.request.Request(URL, headers={"User-Agent": "patch-tests"})
        data = urllib.request.urlopen(req, timeout=120).read()
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    return path


def fresh_upstream(folder):
    """Unpack the clean upstream release into folder and return its custom_components\\jarvis path."""
    zipfile.ZipFile(upstream_zip()).extractall(folder)
    return os.path.join(folder, "jarvis-aio-%s" % VERSION, "custom_components", "jarvis")


def run_kit(base):
    """The same in-process run check_update.py uses: every patch, without the www copy."""
    s = open(os.path.join(KIT, "apply.py"), encoding="utf-8").read()
    head = s[:s.index("# 1 ---")]
    head = head.replace('BASE = sys.argv[1] if len(sys.argv) > 1 else "/config/custom_components/jarvis"', "BASE = %r" % base)
    head = head.replace("KIT = os.path.dirname(os.path.abspath(__file__))", "KIT = %r" % KIT)
    g = {"__name__": "tests"}
    exec(compile(head + s[s.index("# 1 ---"):s.index("\n# 10 ")], "apply", "exec"), g)
    return g["results"]


_built = None


def built_dir():
    global _built
    if _built:
        return _built
    env = os.environ.get("JARVIS_BUILT")
    if env:
        _built = env
        return env
    work = tempfile.mkdtemp(prefix="jarvis-tests-")
    base = fresh_upstream(work)
    failed = [r for r in run_kit(base) if r.startswith("[FAILED")]
    if failed:
        raise RuntimeError("patch kit failed: %s" % failed[:3])
    _built = base
    return base


# ── Home Assistant stand-ins ────────────────────────────────────────────────────────────────────

class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return mock.MagicMock(name=name)


class Stub(metaclass=_Meta):
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return mock.MagicMock(name=name)


def _passthrough(*a, **k):
    if len(a) == 1 and callable(a[0]) and not k:
        return a[0]
    return lambda fn: fn


class FakeModule(types.ModuleType):
    __path__ = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if self.__name__ in ("homeassistant", "homeassistant.components", "homeassistant.helpers") and name[:1].islower():
            value = importlib.import_module(self.__name__ + "." + name)
        else:
            value = type(name, (Stub,), {}) if name[:1].isupper() else mock.MagicMock(name=name)
        setattr(self, name, value)
        return value


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    ROOTS = ("homeassistant", "voluptuous", "aiohttp", "yaml")

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.ROOTS:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        return FakeModule(spec.name)

    def exec_module(self, module):
        pass


def _color_name_to_rgb(name):
    table = {"red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255), "white": (255, 255, 255)}
    if name not in table:
        raise ValueError(name)
    return table[name]


class _Registry:
    """Stands in for the entity, device and area registries. Reads what the test put on hass."""
    def __init__(self, hass):
        self.hass = hass

    def async_get(self, key):
        return getattr(self.hass, "registry_entries", {}).get(key) or getattr(self.hass, "registry_devices", {}).get(key)

    @property
    def entities(self):
        return getattr(self.hass, "registry_entries", {})

    def async_list_areas(self):
        return getattr(self.hass, "registry_areas", [])


def install_stubs():
    if getattr(sys, "_jh_stubs", False):
        return
    sys._jh_stubs = True
    sys.meta_path.insert(0, _Finder())
    os.environ.setdefault("JARVIS_CONFIG_DIR", tempfile.mkdtemp(prefix="jarvis-test-config-"))
    core = importlib.import_module("homeassistant.core")
    core.callback = _passthrough
    ws = importlib.import_module("homeassistant.components.websocket_api")
    ws.websocket_command = _passthrough
    ws.async_response = _passthrough
    ws.require_admin = _passthrough
    importlib.import_module("homeassistant.util.color").color_name_to_rgb = _color_name_to_rgb
    for mod in ("entity_registry", "device_registry", "area_registry"):
        importlib.import_module("homeassistant.helpers." + mod).async_get = _Registry
    vol = importlib.import_module("voluptuous")
    vol.ALLOW_EXTRA, vol.PREVENT_EXTRA = "allow", "prevent"


def load(name):
    """Import one JARVIS module from the patched copy, under a package that skips the real __init__.py."""
    install_stubs()
    if "jc" not in sys.modules:
        pkg = types.ModuleType("jc")
        pkg.__path__ = [built_dir()]
        sys.modules["jc"] = pkg
    return importlib.import_module("jc." + name)


class State:
    def __init__(self, entity_id, state, **attributes):
        self.entity_id, self.state, self.attributes = entity_id, state, attributes


class States:
    def __init__(self):
        self.d = {}

    def set(self, entity_id, state, **attributes):
        self.d[entity_id] = State(entity_id, state, **attributes)

    def get(self, entity_id):
        return self.d.get(entity_id)

    def async_all(self, domain=None):
        return [s for e, s in self.d.items() if domain is None or e.startswith(domain + ".")]


class User:
    def __init__(self, uid, is_admin):
        self.id, self.is_admin = uid, is_admin


class Auth:
    def __init__(self):
        self.users = {}

    async def async_get_user(self, uid):
        return self.users.get(uid)


class Hass:
    def __init__(self):
        self.states = States()
        self.calls = []
        self.auth = Auth()
        self.data = {}
        self.registry_entries = {}
        self.registry_devices = {}
        self.registry_areas = []
        self.services = types.SimpleNamespace(async_call=self._call)
        self.config = types.SimpleNamespace(path=lambda *p: os.path.join(os.environ["JARVIS_CONFIG_DIR"], *p),
                                            external_url=None, internal_url=None)

    async def _call(self, domain, service, data=None, **kw):
        self.calls.append((domain, service, dict(data or {})))

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class Connection:
    """Records what a websocket handler sends back."""
    def __init__(self, user):
        self.user, self.sent, self.errors = user, [], []

    def send_result(self, msg_id, result=None):
        self.sent.append(result)

    def send_error(self, msg_id, code, message):
        self.errors.append(code)


def run(coro):
    return asyncio.run(coro)
