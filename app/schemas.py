"""Pydantic models: the shape of data coming in and going out of the API."""

from datetime import date as date_type
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import TransactionType

# Money is stored as Numeric(12, 2), so cap inputs at what the column can hold.
MAX_AMOUNT = Decimal("9999999999.99")


class TransactionBase(BaseModel):
    type: TransactionType
    amount: Decimal = Field(gt=0, le=MAX_AMOUNT, description="Positive amount")
    category: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    date: date_type

    @field_validator("amount")
    @classmethod
    def round_money(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.01"))

    @field_validator("category")
    @classmethod
    def strip_category(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("category cannot be blank")
        return v


class TransactionCreate(TransactionBase):
    pass


class TransactionUpdate(BaseModel):
    """Every field optional — only what's sent gets changed."""

    model_config = ConfigDict(extra="forbid")

    type: TransactionType | None = None
    amount: Decimal | None = Field(default=None, gt=0, le=MAX_AMOUNT)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    date: date_type | None = None


class TransactionOut(TransactionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class TransactionPage(BaseModel):
    """A slice of transactions plus the total count, so the UI can paginate."""

    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: TransactionType


# --- Analytics -------------------------------------------------------------


class Summary(BaseModel):
    total_income: Decimal
    total_expense: Decimal
    net: Decimal
    savings_rate: float = Field(description="Net as a percentage of income, 0 if none")
    transaction_count: int
    start_date: date_type | None
    end_date: date_type | None


class CategoryTotal(BaseModel):
    category: str
    total: Decimal
    share: float = Field(description="Percentage of total spend in the window")


class MonthlyPoint(BaseModel):
    month: str = Field(description="YYYY-MM")
    income: Decimal
    expense: Decimal
    net: Decimal


# --- Budgets ---------------------------------------------------------------


class BudgetBase(BaseModel):
    category: str = Field(min_length=1, max_length=64)
    monthly_limit: Decimal = Field(gt=0, le=MAX_AMOUNT)

    @field_validator("category")
    @classmethod
    def strip_category(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("category cannot be blank")
        return v

    @field_validator("monthly_limit")
    @classmethod
    def round_money(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.01"))


class BudgetCreate(BudgetBase):
    pass


class BudgetOut(BudgetBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class BudgetStatus(BaseModel):
    """A budget plus how much of it this month's spending has used up."""

    category: str
    monthly_limit: Decimal
    spent: Decimal
    remaining: Decimal
    percent_used: float
    status: str = Field(description="ok | warning | over")
    month: str


# --- AI --------------------------------------------------------------------


class InsightsOut(BaseModel):
    insights: str
    generated_from: Summary
    model: str


class ChatMessageIn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Prior turns, oldest first. The client owns the history; the server is stateless.
    history: list[ChatMessageIn] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    reply: str
    model: str


class CategorizeRequest(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    type: TransactionType = TransactionType.EXPENSE
    amount: Decimal | None = Field(default=None, gt=0, le=MAX_AMOUNT)

    @field_validator("description")
    @classmethod
    def strip_description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("description cannot be blank")
        return v


class CategorizeResponse(BaseModel):
    category: str
    confidence: str = Field(description="high | medium | low")
    reason: str
    model: str


# --- Forecasting -----------------------------------------------------------


class CategoryForecast(BaseModel):
    """What one category is expected to cost next month, and how sure we are."""

    category: str
    type: TransactionType
    kind: str = Field(description="fixed (a recurring bill) | variable")
    average: Decimal = Field(description="Mean over the months used")
    forecast: Decimal = Field(description="Expected total next month")
    change: Decimal = Field(description="Forecast minus average")
    change_percent: float
    trend: str = Field(description="rising | falling | steady")
    confidence: str = Field(description="high | medium | low")
    months_observed: int = Field(description="Months in the window with any spend")


class MonthProjection(BaseModel):
    month: str = Field(description="YYYY-MM")
    projected_income: Decimal
    projected_expense: Decimal
    projected_net: Decimal
    cumulative_net: Decimal = Field(description="Running total across the horizon")


class CurrentMonthProjection(BaseModel):
    """Where the month in progress is heading, given how far into it we are."""

    month: str
    days_elapsed: int
    days_in_month: int
    progress: float = Field(description="Percent of the month gone")
    income_so_far: Decimal
    expense_so_far: Decimal
    projected_income: Decimal
    projected_expense: Decimal
    projected_net: Decimal


class RecurringCharge(BaseModel):
    """A bill that lands every month for roughly the same amount."""

    category: str
    type: TransactionType
    typical_amount: Decimal
    months_seen: int
    typical_day: int = Field(description="Day of the month it usually lands")


class BudgetRisk(BaseModel):
    """A budget measured against where this month is projected to end, not just
    what has been spent so far."""

    category: str
    monthly_limit: Decimal
    spent_so_far: Decimal
    projected_spend: Decimal
    projected_overage: Decimal
    status: str = Field(description="on_track | at_risk | will_exceed")


class Forecast(BaseModel):
    generated_on: date_type
    method: str
    months_of_history: int
    history_from: str | None = Field(description="First month used, YYYY-MM")
    history_to: str | None = Field(description="Last month used, YYYY-MM")
    confidence: str = Field(description="high | medium | low, overall")
    current_month: CurrentMonthProjection
    categories: list[CategoryForecast]
    horizon: list[MonthProjection]
    recurring: list[RecurringCharge]
    budget_risk: list[BudgetRisk]
    notes: list[str] = Field(description="Caveats worth showing next to the numbers")


class ForecastNarrative(BaseModel):
    narrative: str
    model: str
