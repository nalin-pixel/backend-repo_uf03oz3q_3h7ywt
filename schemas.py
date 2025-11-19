"""
Database Schemas for FindRival (MVP v1.0)

Each Pydantic model represents a collection in MongoDB. Class name lowercased
is used as the collection name by convention.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any

class GeoPoint(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: List[float] = Field(..., min_items=2, max_items=2, description="[lng, lat]")

class Availability(BaseModel):
    days: List[Literal["mon","tue","wed","thu","fri","sat","sun"]] = []
    timeslot: Optional[Literal["morning","afternoon","evening","any"]] = "any"

class Team(BaseModel):
    owner_uid: str = Field(..., description="Firebase Auth user UID for the team owner")
    name: str
    sport: Literal["soccer","basketball","tennis","cricket","volleyball","badminton","rugby","hockey","other"]
    location: GeoPoint
    address: Optional[str] = None
    players: List[str] = []
    availability: Availability = Availability()
    device_tokens: List[str] = []

class MatchRequest(BaseModel):
    from_team_id: str
    to_team_id: str
    status: Literal["pending","accepted","rejected","confirmed"] = "pending"
    proposed_time: Optional[str] = None
    notes: Optional[str] = None

# Response models (lightweight)
class TeamPublic(BaseModel):
    id: str
    name: str
    sport: str
    location: Dict[str, Any]
    address: Optional[str] = None
    players: List[str] = []
    availability: Availability = Availability()

class MatchRequestPublic(BaseModel):
    id: str
    from_team_id: str
    to_team_id: str
    status: str
    proposed_time: Optional[str] = None
    notes: Optional[str] = None
