"""Import existing trails or proposed alignments from a vector file.

    manage.py import_lines 260927_Trails.zip --as trails
    manage.py import_lines 250924_Scouting_plans.gpkg --as alignments \\
        --project-name "Scouting plans 2025-09" --owner hig
    manage.py import_lines FILE --as trails --dry-run

Any format GDAL reads: zipped or bare shapefile, GeoPackage, GeoJSON, KML.
Re-running on the same file updates in place (see core/importers.py).
The same importer backs the /import/ page for trail editors.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.importers import ImportError_, import_alignments, import_trails
from core.models import Project, Visibility


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--as", dest="target", choices=["trails", "alignments"], required=True)
        parser.add_argument("--project", help="Slug of an existing project (alignments).")
        parser.add_argument("--project-name", help="Name for a new project (alignments).")
        parser.add_argument("--visibility", choices=[v for v, _ in Visibility.choices], default=Visibility.PRIVATE,
                            help="Visibility of a new project (default private).")
        parser.add_argument("--owner", help="Username to own the created rows.")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-evaluate", action="store_true",
                            help="Do not queue an evaluation of each imported alignment.")

    def handle(self, *args, **o):
        owner = None
        if o["owner"]:
            owner = get_user_model().objects.filter(username=o["owner"]).first()
            if owner is None:
                raise CommandError(f"no user {o['owner']!r}")
        try:
            if o["target"] == "trails":
                rep = import_trails(o["path"], owner=owner, dry_run=o["dry_run"])
            else:
                project = None
                if o["project"]:
                    project = Project.objects.filter(slug=o["project"]).first()
                    if project is None:
                        raise CommandError(f"no project {o['project']!r}")
                rep = import_alignments(o["path"], project=project, project_name=o["project_name"],
                                        owner=owner, visibility=o["visibility"], dry_run=o["dry_run"],
                                        evaluate=not o["no_evaluate"])
        except ImportError_ as e:
            raise CommandError(str(e)) from e
        self.stdout.write(("dry run: " if o["dry_run"] else "") + str(rep))
