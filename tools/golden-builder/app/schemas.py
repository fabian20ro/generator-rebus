from pydantic import BaseModel, Field


class PairRow(BaseModel):
    puzzle_title: str
    solution: str = ""
    definition: str = ""


class ExtractResponse(BaseModel):
    rows: list[PairRow]
    warnings: list[str] = Field(default_factory=list)


class SaveRequest(BaseModel):
    puzzle_title: str
    rows: list[PairRow]


class MergeRequest(BaseModel):
    pass
