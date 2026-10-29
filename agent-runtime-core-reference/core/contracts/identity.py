from pydantic import BaseModel, ConfigDict


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)
    principal_id: str
    role: str = "reviewer"
    scope: str = "fictional-procurement"


class RequestContext(BaseModel):
    principal: Principal
    request_id: str
