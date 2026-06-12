from pydantic import BaseModel


class AlgoStartRequest(BaseModel):
    symbol: str = ""
    api_key: str = ""
    api_secret: str = ""
    trade_setup: str | dict = "{}"
    trail: bool = False
    read_only: bool = False


class AlgoPauseRequest(BaseModel):
    symbol: str = ""


class AlgoStatusResponse(BaseModel):
    success: bool = True
    status: dict = {}


class AlgoPricesRequest(BaseModel):
    symbols: list[str] = []


class AlgoPricesResponse(BaseModel):
    success: bool = True
    prices: dict[str, float] = {}
