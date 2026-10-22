from backend.app.api.admin.base import *

@router.get("/households")
def households(_admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(Household).order_by(Household.created_at)).all()
    members = db.execute(select(HouseholdMember.household_id, HouseholdMember.account_id,
                                 HouseholdMember.role, Account.username).join(
        Account, Account.id == HouseholdMember.account_id)).all()
    grouped: dict[str, list] = {}
    for household_id, account_id, role, username in members:
        grouped.setdefault(household_id, []).append(
            {"account_id": account_id, "username": username, "role": role})
    return [{"id": row.id, "name": row.name, "created_at": row.created_at,
             "members": grouped.get(row.id, [])} for row in rows]
