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
