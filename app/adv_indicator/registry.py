import importlib
import pkgutil
import logging

from .base import Indicator, IndicatorMeta

logger = logging.getLogger(__name__)

_registry: dict[str, Indicator] = {}


def _discover():
    import app.adv_indicator as pkg

    for _, mod_name, _ in pkgutil.iter_modules(pkg.__path__):
        if mod_name == "base" or mod_name == "registry":
            continue
        try:
            mod = importlib.import_module(f"app.adv_indicator.{mod_name}")
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and issubclass(attr, Indicator) and attr is not Indicator:
                    inst = attr()
                    _registry[inst.meta.id] = inst
                    logger.info("Loaded indicator: %s", inst.meta.id)
        except Exception as e:
            logger.warning("Failed to load indicator module %s: %s", mod_name, e)


def list_indicators() -> list[IndicatorMeta]:
    if not _registry:
        _discover()
    return [inst.meta for inst in _registry.values()]


def compute(indicator_id: str, times: list, close: list, high: list, low: list, open: list = None, params: dict = None) -> dict:
    if not _registry:
        _discover()
    inst = _registry.get(indicator_id)
    if inst is None:
        raise ValueError(f"Unknown indicator: {indicator_id}")
    return inst.compute(times, close, high, low, open=open, params=params)
