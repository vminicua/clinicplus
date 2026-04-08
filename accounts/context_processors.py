from accounts.ui import available_branches_for_user, branch_label, get_system_preferences


def clinic_shell_context(request):
    user = getattr(request, "user", None)
    available_branches = available_branches_for_user(user) if user and user.is_authenticated else []
    current_branch = getattr(request, "clinic_current_branch", None)

    return {
        "available_branches": available_branches,
        "current_branch": current_branch,
        "current_branch_label": branch_label(current_branch),
        "system_preferences": get_system_preferences(),
    }
