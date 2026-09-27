"""Role groups, in the same shape as landslidescience's ROLE_GROUPS.
`manage.py init_groups` creates them idempotently; the entrypoint runs it on
every web start, so adding a role here is all a deploy needs.

The permissions only matter inside /admin/ (which also needs is_staff).
The site's own views ask core.access instead, by group membership."""

_ALL = ["add", "change", "delete", "view"]

ROLE_GROUPS = {
    # Every signed-in collaborator: can see gated DEMs and gated trails,
    # tracks and projects.
    "data_users": [],
    # Can create and edit trails, tracks, projects and alignments.
    "trail_editors": [
        ("core", "trail", _ALL),
        ("core", "track", _ALL),
        ("core", "project", _ALL),
        ("core", "alignment", _ALL),
        ("core", "leg", _ALL),
        ("core", "demsource", ["view"]),
    ],
    # Can edit Page content in /admin/.
    "site_admins": [
        ("pages", "page", _ALL),
    ],
}
