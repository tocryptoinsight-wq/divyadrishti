from dataclasses import dataclass, field


@dataclass
class IndicatorLine:
    id: str
    name: str
    color: str


@dataclass
class FillDef:
    top_line_id: str
    bottom_line_id: str
    color: str
    opacity: float = 0.25


@dataclass
class ParamDef:
    id: str
    name: str
    type: str  # "int", "float", "color", "select"
    default: any
    options: list = None
    min: float = None
    max: float = None


@dataclass
class IndicatorMeta:
    id: str
    name: str
    lines: list[IndicatorLine]
    fills: list[FillDef] = field(default_factory=list)
    params: list[ParamDef] = field(default_factory=list)


class Indicator:
    meta: IndicatorMeta

    def compute(self, times: list, close: list, high: list, low: list, open: list = None, params: dict = None) -> dict[str, list]:
        raise NotImplementedError

    def get_params(self) -> list[ParamDef]:
        return self.meta.params
