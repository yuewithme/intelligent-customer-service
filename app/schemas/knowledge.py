from pydantic import BaseModel


class KnowledgeUploadData(BaseModel):
    doc_id: str
    file_name: str
    chunk_count: int
    status: str

