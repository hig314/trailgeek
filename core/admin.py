from django.contrib import admin
from django.contrib.gis import admin as gis_admin

from .models import Alignment, DemSource, Leg, Project, Trail, Track


@admin.register(DemSource)
class DemSourceAdmin(gis_admin.GISModelAdmin):
    list_display = ("slug", "title", "kind", "year", "region", "native_res_m", "enabled", "gated")
    list_filter = ("kind", "enabled", "gated", "region")
    search_fields = ("slug", "title", "source", "notes")
    readonly_fields = ("imported_at",)


@admin.register(Trail)
class TrailAdmin(gis_admin.GISModelAdmin):
    list_display = ("name", "slug", "status", "trail_class", "region", "visibility", "length_km", "updated")
    list_filter = ("status", "trail_class", "visibility", "region", "builder")
    search_fields = ("name", "slug", "description", "source_key")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("length_m",)

    @admin.display(description="km")
    def length_km(self, obj):
        return f"{obj.length_m / 1000:.2f}"


@admin.register(Track)
class TrackAdmin(gis_admin.GISModelAdmin):
    list_display = ("name", "taken_at", "trail", "visibility", "length_km", "climb_m", "owner")
    list_filter = ("visibility", "has_elevation")
    search_fields = ("name", "description", "device")
    readonly_fields = ("length_m", "point_count", "climb_m", "descent_m", "times")

    @admin.display(description="km")
    def length_km(self, obj):
        return f"{obj.length_m / 1000:.2f}"


class AlignmentInline(admin.TabularInline):
    model = Alignment
    fields = ("name", "priority", "trailhead", "length_m", "evaluation_status")
    readonly_fields = ("length_m", "evaluation_status")
    extra = 0
    show_change_link = True


@admin.register(Project)
class ProjectAdmin(gis_admin.GISModelAdmin):
    list_display = ("name", "slug", "dem", "visibility", "owner", "updated")
    list_filter = ("visibility",)
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("members",)
    inlines = [AlignmentInline]


class LegInline(admin.TabularInline):
    """Leg geometry is edited on the map; here only its attributes."""
    model = Leg
    fields = ("order", "name", "kind", "trail", "effort_factor", "length_m")
    readonly_fields = ("length_m",)
    raw_id_fields = ("trail",)
    extra = 0


@admin.register(Alignment)
class AlignmentAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "priority", "trailhead", "length_m", "evaluation_status", "parent")
    list_filter = ("project", "evaluation_status")
    search_fields = ("name", "notes", "source")
    readonly_fields = ("length_m", "runs_uphill", "evaluation_status", "evaluated_at", "evaluation_error")
    raw_id_fields = ("parent",)
    inlines = [LegInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.rebuild_from_legs()
