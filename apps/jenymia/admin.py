from django.contrib import admin

from .models import ProductToAnalyse, WebUrl, YoutubeUrl


class SourceUrlInline(admin.TabularInline):
    """Shared layout for the URL inlines: the admin enters url and date,
    the pipeline fills raw_text, so that field stays read-only here."""

    extra = 1
    fields = ("url", "source_date", "raw_text")
    readonly_fields = ("raw_text",)


class YoutubeUrlInline(SourceUrlInline):
    model = YoutubeUrl
    verbose_name = "YouTube URL"
    verbose_name_plural = "YouTube URLs"


class WebUrlInline(SourceUrlInline):
    model = WebUrl
    verbose_name = "Web URL"
    verbose_name_plural = "Web URLs"


@admin.register(ProductToAnalyse)
class ProductToAnalyseAdmin(admin.ModelAdmin):
    list_display = ("title", "brand", "status", "analysed_by", "creator", "last_analysed_at")
    list_filter = ("status", "analysed_by")
    search_fields = ("title", "brand")
    ordering = ("-created_at",)
    readonly_fields = ("creator", "last_analysed_at", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("title", "brand")}),
        ("Analysis", {"fields": ("status", "analysed_by", "last_analysed_at")}),
        ("Meta", {"fields": ("creator", "created_at", "updated_at")}),
    )
    inlines = (YoutubeUrlInline, WebUrlInline)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("creator")

    def save_model(self, request, obj, form, change):
        # Entries made here are always admin-created; the creator is the
        # logged-in user, never editable by hand.
        if not change:
            obj.creator = request.user
            obj.analysed_by = ProductToAnalyse.AnalysedBy.ADMIN
        super().save_model(request, obj, form, change)
