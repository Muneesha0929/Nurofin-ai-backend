from app.api.v1.endpoints.targets import can_manage_all_targets
from app.models.user import User

def test_can_manage_all_targets():
    user1 = User(id=1, role="ceo")
    assert can_manage_all_targets(user1) == True
    
    user2 = User(id=2, role="super_admin")
    assert can_manage_all_targets(user2) == True
    
    user3 = User(id=3, role="team_lead")
    assert can_manage_all_targets(user3) == True
    
    user4 = User(id=4, role="employee")
    assert can_manage_all_targets(user4) == False
