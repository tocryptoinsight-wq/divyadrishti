from pydantic import BaseModel


class AuthRequest(BaseModel):
    api_key: str = ""
    api_secret: str = ""


class BalanceRequest(AuthRequest):
    pass


class BalanceResponse(BaseModel):
    success: bool
    balances: list = []
    error: str = ""


class PositionsRequest(AuthRequest):
    underlying_asset_symbol: str = ""


class PositionsResponse(BaseModel):
    success: bool
    positions: list = []
    error: str = ""


class TradeHistoryRequest(AuthRequest):
    limit: int = 10


class TradeHistoryResponse(BaseModel):
    success: bool
    trades: list = []
    error: str = ""


class OpenOrdersRequest(AuthRequest):
    symbol: str = ""


class OpenOrdersResponse(BaseModel):
    success: bool
    orders: list = []
    error: str = ""


class ClosePositionRequest(AuthRequest):
    symbol: str = ""
    size: float = 0.0
    read_only: bool = False


class ClosePositionResponse(BaseModel):
    success: bool
    result: dict | list | None = None
    error: str = ""


class CancelOrdersRequest(AuthRequest):
    symbol: str = ""
    read_only: bool = False


class CancelOrdersResponse(BaseModel):
    success: bool
    cancelled: int = 0
    error: str = ""


class ExecuteTradeRequest(AuthRequest):
    symbol: str = ""
    side: str = ""
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    qty: float | None = None
    sl_dist: float | None = None
    dry_run: bool = False
    read_only: bool = False


class ExecuteTradeResponse(BaseModel):
    success: bool
    error: str = ""
    sent_qty: int = 0
    product_id: int = 0
    contract_value: float = 1.0
    dry_run: bool = False
    payloads: list = []
    results: list = []
    actual_entry: float = 0.0
    partial_warning: str = ""



