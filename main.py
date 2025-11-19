import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import Team, TeamPublic, MatchRequest, MatchRequestPublic

# Firebase Admin
import firebase_admin
from firebase_admin import credentials, auth, messaging

# Initialize Firebase Admin using environment vars
# Expect a base64 JSON service account or application default
FIREBASE_CREDENTIALS_B64 = os.getenv("FIREBASE_CREDENTIALS_B64")
if not firebase_admin._apps:
    try:
        if FIREBASE_CREDENTIALS_B64:
            import base64, json
            sa_json = json.loads(base64.b64decode(FIREBASE_CREDENTIALS_B64).decode("utf-8"))
            cred = credentials.Certificate(sa_json)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()
    except Exception as e:
        # Firebase optional for local dev; continue without hard failure
        print("Firebase init warning:", e)

app = FastAPI(title="FindRival API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Utilities
class AuthHeader(BaseModel):
    authorization: Optional[str] = None

def verify_firebase_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    try:
        decoded = auth.verify_id_token(token)
        return decoded.get("uid")
    except Exception:
        return None

# Routes
@app.get("/")
def root():
    return {"service": "FindRival API", "version": "1.0.0"}

# 1) Team creation & listing
@app.post("/teams", response_model=TeamPublic)
def create_team(team: Team):
    # Ensure 2dsphere index exists
    try:
        db.team.create_index([("location", "2dsphere")])
    except Exception:
        pass

    inserted_id = create_document("team", team)
    return TeamPublic(
        id=inserted_id,
        name=team.name,
        sport=team.sport,
        location=team.location.model_dump(),
        address=team.address,
        players=team.players,
        availability=team.availability,
    )

@app.get("/teams", response_model=List[TeamPublic])
def list_teams(sport: Optional[str] = None):
    filt = {"sport": sport} if sport else {}
    teams = get_documents("team", filt)
    result = []
    for t in teams:
        result.append(
            TeamPublic(
                id=str(t.get("_id")),
                name=t.get("name"),
                sport=t.get("sport"),
                location=t.get("location"),
                address=t.get("address"),
                players=t.get("players", []),
                availability=t.get("availability", {}),
            )
        )
    return result

# 2) Nearby opponent finder (GPS + filters)
@app.get("/teams/nearby", response_model=List[TeamPublic])
def nearby_teams(
    lng: float,
    lat: float,
    max_km: float = 25.0,
    sport: Optional[str] = None,
    timeslot: Optional[str] = None,
):
    geo_filter = {
        "location": {
            "$near": {
                "$geometry": {"type": "Point", "coordinates": [lng, lat]},
                "$maxDistance": int(max_km * 1000),
            }
        }
    }
    filt = geo_filter
    if sport:
        filt["sport"] = sport
    if timeslot:
        filt["availability.timeslot"] = timeslot

    try:
        docs = db.team.find(filt).limit(50)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Geo query failed: {e}")

    result = []
    for t in docs:
        result.append(
            TeamPublic(
                id=str(t.get("_id")),
                name=t.get("name"),
                sport=t.get("sport"),
                location=t.get("location"),
                address=t.get("address"),
                players=t.get("players", []),
                availability=t.get("availability", {}),
            )
        )
    return result

# 3) Match request system
@app.post("/match-requests", response_model=MatchRequestPublic)
def send_match_request(req: MatchRequest):
    # Basic validation: ensure teams exist
    for tid in [req.from_team_id, req.to_team_id]:
        if not db.team.find_one({"_id": ObjectId(tid)}):
            raise HTTPException(status_code=404, detail=f"Team {tid} not found")

    rid = create_document("matchrequest", req)

    # Push notification to target team (if tokens available)
    try:
        to_team = db.team.find_one({"_id": ObjectId(req.to_team_id)})
        tokens = to_team.get("device_tokens", []) if to_team else []
        if tokens:
            message = messaging.MulticastMessage(
                tokens=tokens,
                notification=messaging.Notification(
                    title="New Match Request",
                    body="You have a new match request",
                ),
                data={
                    "type": "match_request",
                    "id": rid,
                },
            )
            messaging.send_multicast(message)
    except Exception as e:
        print("FCM send warning:", e)

    return MatchRequestPublic(
        id=rid,
        from_team_id=req.from_team_id,
        to_team_id=req.to_team_id,
        status=req.status,
        proposed_time=req.proposed_time,
        notes=req.notes,
    )

@app.post("/match-requests/{request_id}/accept", response_model=MatchRequestPublic)
def accept_request(request_id: str):
    res = db.matchrequest.update_one({"_id": ObjectId(request_id)}, {"$set": {"status": "accepted"}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Request not found")
    req = db.matchrequest.find_one({"_id": ObjectId(request_id)})
    return _req_public(req)

@app.post("/match-requests/{request_id}/reject", response_model=MatchRequestPublic)
def reject_request(request_id: str):
    res = db.matchrequest.update_one({"_id": ObjectId(request_id)}, {"$set": {"status": "rejected"}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Request not found")
    req = db.matchrequest.find_one({"_id": ObjectId(request_id)})
    return _req_public(req)

@app.post("/match-requests/{request_id}/confirm", response_model=MatchRequestPublic)
def confirm_request(request_id: str):
    res = db.matchrequest.update_one({"_id": ObjectId(request_id)}, {"$set": {"status": "confirmed"}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Request not found")
    req = db.matchrequest.find_one({"_id": ObjectId(request_id)})
    return _req_public(req)

@app.get("/match-requests", response_model=List[MatchRequestPublic])
def list_match_requests(team_id: Optional[str] = None):
    filt = {}
    if team_id:
        filt = {"$or": [{"from_team_id": team_id}, {"to_team_id": team_id}]}
    docs = db.matchrequest.find(filt).limit(100)
    return [_req_public(d) for d in docs]

# Helpers

def _req_public(d) -> MatchRequestPublic:
    return MatchRequestPublic(
        id=str(d.get("_id")),
        from_team_id=d.get("from_team_id"),
        to_team_id=d.get("to_team_id"),
        status=d.get("status"),
        proposed_time=d.get("proposed_time"),
        notes=d.get("notes"),
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
