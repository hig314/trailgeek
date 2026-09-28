from django import forms

from .models import Trail, Track, Visibility

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class TrackUploadForm(forms.Form):
    file = forms.FileField(label="GPX file", help_text="A .gpx track from a GPS or phone app, up to 25 MB.")
    name = forms.CharField(max_length=200, required=False, help_text="Defaults to the track name in the file.")
    trail = forms.ModelChoiceField(
        queryset=Trail.objects.none(), required=False, help_text="The trail this track follows, if any."
    )
    visibility = forms.ChoiceField(choices=Visibility.choices, initial=Visibility.PUBLIC)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from . import access
        qs = Trail.objects.filter(access.visible_q(user)) if user is not None else Trail.objects.none()
        self.fields["trail"].queryset = qs.distinct()

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("File is larger than 25 MB.")
        if not f.name.lower().endswith((".gpx", ".xml")):
            raise forms.ValidationError("Only GPX files are accepted for now (KML and GeoJSON are planned).")
        return f


class TrackEditForm(forms.ModelForm):
    class Meta:
        model = Track
        fields = ["name", "description", "trail", "visibility"]


IMPORT_SUFFIXES = (".zip", ".gpkg", ".geojson", ".json", ".kml")


class LineImportForm(forms.Form):
    """Upload a vector file of trails or proposed alignments (core.importers)."""
    TARGETS = [("trails", "Existing trails (one trail per line; re-importing the same file updates them)"),
               ("alignments", "Proposed alignments (one alignment per line, in a design project)")]
    file = forms.FileField(help_text="Zipped shapefile, GeoPackage, GeoJSON or KML, up to 50 MB.")
    target = forms.ChoiceField(choices=TARGETS, widget=forms.RadioSelect, initial="trails")
    project = forms.ModelChoiceField(queryset=None, required=False,
                                     help_text="Add the alignments to this project…")
    project_name = forms.CharField(max_length=200, required=False,
                                   help_text="…or create a new project with this name.")
    visibility = forms.ChoiceField(choices=Visibility.choices, initial=Visibility.PRIVATE,
                                   help_text="For a new project. Private: only you and project members.")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from . import access
        from .models import Project
        self.fields["project"].queryset = (Project.objects.filter(access.visible_q(user)).distinct()
                                           if user is not None else Project.objects.none())

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 50 * 1024 * 1024:
            raise forms.ValidationError("File is larger than 50 MB.")
        if not f.name.lower().endswith(IMPORT_SUFFIXES):
            raise forms.ValidationError("Use a .zip (shapefile), .gpkg, .geojson or .kml file.")
        return f
