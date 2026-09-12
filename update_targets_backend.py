import re

with open('app/api/v1/endpoints/targets.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Update is_ceo_or_admin
content = content.replace(
    'return user.role in ["ceo", "super_admin", "CEO"] or user.id == 1',
    'return user.role in ["ceo", "super_admin", "CEO", "team_lead"] or user.id == 1'
)

# Append Delete Endpoint
delete_endpoint = """
@router.delete("/{target_id}", response_model=dict)
async def delete_target(
    *,
    db: AsyncSession = Depends(deps.get_db),
    target_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Target).filter(Target.id == target_id))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    if target.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions to delete this target")

    await db.delete(target)
    await db.commit()
    return {"success": True}
"""

if "def delete_target(" not in content:
    content += "\n" + delete_endpoint

with open('app/api/v1/endpoints/targets.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Backend targets.py updated")
