from accounts.ui import branch_label, cached_available_branches_for_request, get_system_preferences


def clinic_shell_context(request):
    available_branches = cached_available_branches_for_request(request)
    current_branch = getattr(request, "clinic_current_branch", None)

    return {
        "available_branches": available_branches,
        "current_branch": current_branch,
        "current_branch_label": branch_label(current_branch),
        "system_preferences": get_system_preferences(),
    }
