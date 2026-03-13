from typing import List, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
import time
import uuid
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from backend.user.dependencies import get_current_user, require_user, User
from backend.mongodb.db import db
from backend.models import ModelConfig, CreateModelRequest, UpdateModelRequest, list_user_models

router = APIRouter(prefix="/models", tags=["models"])

class ApiResponse(BaseModel):
    code: int = Field(default=0)
    msg: str = Field(default="ok")
    data: Any = Field(default=None)

async def verify_model_connection(provider: str, base_url: str | None, api_key: str | None, model_name: str):
    """
    Verify model availability by making a simple request.
    """
    try:
        # Construct ChatOpenAI (supports OpenAI-compatible APIs like DeepSeek, Qwen etc.)
        # Ensure we have minimal config
        if not api_key:
            raise ValueError("API Key is required for verification")
        
        chat = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url if base_url else None,
            max_tokens=5,
            timeout=10 # Short timeout
        )
        
        # Invoke a simple "Hello"
        await chat.ainvoke([HumanMessage(content="Hi")])
        return True
    except Exception as e:
        # Log the error if needed
        raise HTTPException(status_code=400, detail=f"Model verification failed: {str(e)}")

@router.get("", response_model=ApiResponse)
async def list_models(current_user: User = Depends(require_user)):
    """List all available models (System + User Defined)"""
    models = await list_user_models(current_user.id)
    results = []
    for m in models:
        d = m.model_dump()
        if d.get("api_key"):
            d["api_key"] = "********"
        results.append(d)
    return ApiResponse(data=results)

@router.post("", response_model=ApiResponse)
async def create_model(body: CreateModelRequest, current_user: User = Depends(require_user)):
    """Add a user defined model"""
    # Verify first
    await verify_model_connection(body.provider, body.base_url, body.api_key, body.model_name)

    model_id = str(uuid.uuid4())
    now = int(time.time())
    
    new_model = ModelConfig(
        id=model_id,
        name=body.name,
        provider=body.provider,
        base_url=body.base_url,
        api_key=body.api_key,
        model_name=body.model_name,
        context_window=body.context_window,
        is_system=False,
        user_id=current_user.id,
        is_active=True,
        created_at=now,
        updated_at=now
    )
    
    doc = new_model.model_dump()
    doc["_id"] = doc.pop("id")
    
    await db.get_collection("models").insert_one(doc)
    
    # Return with id
    return ApiResponse(data=new_model.model_dump())

@router.put("/{model_id}", response_model=ApiResponse)
async def update_model(model_id: str, body: UpdateModelRequest, current_user: User = Depends(require_user)):
    """Update a user defined model"""
    # Check ownership
    existing = await db.get_collection("models").find_one({"_id": model_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Model not found")
        
    if existing.get("is_system"):
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Cannot edit this model")
    else:
        if existing.get("user_id") != current_user.id:
            raise HTTPException(status_code=403, detail="Cannot edit this model")
        
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        return ApiResponse(data={"id": model_id})
    
    # Verify if connection details changed
    # We merge existing with updates to get full config for verification
    merged_base_url = update_data.get("base_url", existing.get("base_url"))
    merged_api_key = update_data.get("api_key", existing.get("api_key"))
    merged_model_name = update_data.get("model_name", existing.get("model_name"))
    merged_provider = existing.get("provider") # Provider usually doesn't change or isn't in update request?
    # UpdateModelRequest doesn't have provider? Let's check model definition.
    # UpdateModelRequest has name, base_url, api_key, model_name, is_active.
    
    # Only verify if critical fields are updated
    if any(k in update_data for k in ["base_url", "api_key", "model_name"]):
        await verify_model_connection(merged_provider, merged_base_url, merged_api_key, merged_model_name)

    update_data["updated_at"] = int(time.time())
    
    await db.get_collection("models").update_one(
        {"_id": model_id},
        {"$set": update_data}
    )
    
    return ApiResponse(data={"id": model_id})

@router.delete("/{model_id}", response_model=ApiResponse)
async def delete_model(model_id: str, current_user: User = Depends(require_user)):
    """Delete a user defined model"""
    existing = await db.get_collection("models").find_one({"_id": model_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Model not found")
        
    if existing.get("is_system"):
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Cannot delete this model")
    else:
        if existing.get("user_id") != current_user.id:
            raise HTTPException(status_code=403, detail="Cannot delete this model")
        
    await db.get_collection("models").delete_one({"_id": model_id})
    return ApiResponse(data={"ok": True})
