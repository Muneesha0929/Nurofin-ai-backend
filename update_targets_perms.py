import re

with open('app/api/v1/endpoints/targets.py', 'r', encoding='utf-8') as f:
    content = f.read()

delete_permission_endpoint = """
@router.delete("/permissions/{permission_id}", response_model=dict)
async def delete_permission(
    *,
    db: AsyncSession = Depends(deps.get_db),
    permission_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if not is_ceo_or_admin(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    result = await db.execute(select(TargetPermission).filter(TargetPermission.id == permission_id))
    permission = result.scalars().first()
    if not permission:
        raise HTTPException(status_code=404, detail="Permission not found")
        
    await db.delete(permission)
    await db.commit()
    return {"success": True}
"""

if "def delete_permission(" not in content:
    content += "\n" + delete_permission_endpoint

with open('app/api/v1/endpoints/targets.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Backend permissions endpoint updated")
