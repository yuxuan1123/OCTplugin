"""
conversion/services/conversion/converter_factory.py
───────────────────────────────────────────────
转换器工厂（业务逻辑层），1:1 迁移自 OCTools/services/conversion/converter_factory.py。
"""

from typing import Callable, Optional

from core import formats as FMT
from services.conversion.registry import REGISTRY, ConversionSpec
from services.conversion.star.router import router as STAR_ROUTER


class ConverterFactory:
    """自动选择直接 or 星型转换的工厂"""

    @staticmethod
    def find_spec(src: str, dst: str) -> Optional[ConversionSpec]:
        return REGISTRY.get(src, dst)

    @staticmethod
    def find_path(src: str, dst: str) -> list:
        src, dst = FMT.resolve(src), FMT.resolve(dst)
        if REGISTRY.has(src, dst):
            return [src, dst]
        return STAR_ROUTER.find_path(src, dst) or []

    @classmethod
    def build(cls, src: str, dst: str) -> Optional[Callable]:
        path = cls.find_path(src, dst)
        if not path:
            return None
        if len(path) == 2:
            spec = REGISTRY.get(src, dst)
            return _DirectCallable(spec)
        return _StarCallable(path)

    @classmethod
    def convert(cls, input_path, output_path, log=lambda m: print(m),
                config=None, target: Optional[str] = None) -> bool:
        from services.conversion.pipeline import convert as _convert
        return _convert(input_path, output_path, log, config, target)


class _DirectCallable:
    """直达转换 callable"""

    def __init__(self, spec: ConversionSpec):
        self.spec = spec

    def __call__(self, input_path, output_path, log, config=None):
        if self.spec.config_type is None or config is None:
            return self.spec.func(input_path, output_path, log)
        return self.spec.func(input_path, output_path, log,
                              **{self.spec.config_kwarg: config})


class _StarCallable:
    """星型寻路转换 callable"""

    def __init__(self, path: list):
        self.path = path

    def __call__(self, input_path, output_path, log, config=None):
        from services.conversion.star.runner import run_path
        log(f"⭐ 星型自动寻路: {' → '.join(self.path)}")
        return run_path(input_path, output_path, self.path, REGISTRY, log, config)


factory = ConverterFactory()