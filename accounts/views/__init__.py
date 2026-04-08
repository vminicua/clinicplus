from .permissions_view import (
    PermissionCreateView,
    PermissionDetailView,
    PermissionListView,
    PermissionUpdateView,
)
from .preferences_view import BranchSwitchView, LanguageSwitchView, SystemPreferenceView
from .organization_view import (
    BranchCreateView,
    BranchDetailView,
    BranchListView,
    BranchToggleStatusView,
    BranchUpdateView,
)
from .roles_view import (
    RoleCreateView,
    RoleDetailView,
    RoleListView,
    RoleUpdateView,
)
from .users_view import (
    UserCreateView,
    UserDetailView,
    UserListView,
    UserToggleStatusView,
    UserUpdateView,
)

__all__ = [
    "UserListView",
    "UserDetailView",
    "UserCreateView",
    "UserUpdateView",
    "UserToggleStatusView",
    "RoleListView",
    "RoleDetailView",
    "RoleCreateView",
    "RoleUpdateView",
    "PermissionListView",
    "PermissionDetailView",
    "PermissionCreateView",
    "PermissionUpdateView",
    "BranchListView",
    "BranchDetailView",
    "BranchCreateView",
    "BranchUpdateView",
    "BranchToggleStatusView",
    "SystemPreferenceView",
    "LanguageSwitchView",
    "BranchSwitchView",
]
