"""Role groups, in the same shape as landslidescience's ROLE_GROUPS.
`manage.py init_groups` creates them idempotently; the entrypoint runs it on
every web start, so adding a role here is all a deploy needs."""

ROLE_GROUPS = {
    # Every signed-in collaborator: can see gated DEMs and private projects
    # they are a member of.
    "data_users": [],
    # Can create and edit trails, tracks, projects and alignments.
    "trail_editors": [],
    # Can edit Page content in /admin/.
    "site_admins": [
        ("pages", "page", ["add", "change", "delete", "view"]),
    ],
}
