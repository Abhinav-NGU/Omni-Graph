from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class GraphEntity(BaseModel):
    """Represents a single entity or relationship extracted from text."""
    source: str = Field(description="The source entity.")
    target: str = Field(description="The target entity.")
    relationship: str = Field(description="The relationship between the source and target entities.")
    properties: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional properties of the relationship.")

class ExtractedGraph(BaseModel):
    """Represents the full graph structure extracted from a text chunk."""
    entities: List[GraphEntity] = Field(description="A list of entities and their relationships.")