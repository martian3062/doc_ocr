"""
Pipeline Forms — file upload and run configuration.
=====================================================
Three forms used by the upload and run-start views:

  PDFUploadForm     — multi-file upload (.pdf, .json, .txt)
  FolderImportForm  — server-side folder path input
  PipelineRunForm   — pipeline run name / model / quantisation options
"""

from django import forms


class MultiFileInput(forms.FileInput):
    """
    FileInput widget that accepts multiple files in one <input>.

    Setting allow_multiple_selected = True tells Django's FileField
    not to reject multi-file submissions (standard Django behaviour
    only accepts one file per FileField).
    """
    allow_multiple_selected = True


class PDFUploadForm(forms.Form):
    """
    Upload one or more files for import.

    Accepted types: .pdf (pipeline source), .json (_final.json exports),
    .txt / .text (plain-text documents).

    The hidden class on the input is intentional — the actual trigger is
    a drag-and-drop zone rendered in the upload.html template via Alpine.js.
    """
    files = forms.FileField(
        widget=MultiFileInput(attrs={
            "accept":   ".pdf,.txt,.text,.json",
            "class":    "hidden",
            "id":       "file-upload-input",
        }),
        required=False,
    )


class FolderImportForm(forms.Form):
    """
    Accept a server-side directory path for bulk PDF / JSON import.

    The path is validated in the view (import_folder) — the form itself
    only performs basic type/length validation.
    """
    folder_path = forms.CharField(
        max_length=1000,
        widget=forms.TextInput(attrs={
            "placeholder": "e.g. /home/pardeep/data/TMH_Patient_Reports",
            "class": (
                "w-full px-4 py-3 bg-white/5 border border-purple-300/20 "
                "rounded-xl text-white placeholder-gray-400 "
                "focus:ring-2 focus:ring-purple-400 focus:border-transparent"
            ),
        }),
    )


class PipelineRunForm(forms.Form):
    """
    Configure a new pipeline run.

    Fields
    ------
    name      : human-readable run label (optional, auto-generated if blank)
    model_id  : HuggingFace model ID to use for LLM extraction
    use_4bit  : enable 4-bit NF4 quantisation (recommended, halves VRAM usage)
    """
    name = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Run name (optional)",
            "class": (
                "w-full px-4 py-3 bg-white/5 border border-purple-300/20 "
                "rounded-xl text-white placeholder-gray-400 "
                "focus:ring-2 focus:ring-purple-400"
            ),
        }),
    )
    model_id = forms.CharField(
        max_length=200,
        initial="Qwen/Qwen2.5-1.5B-Instruct",
        widget=forms.TextInput(attrs={
            "class": (
                "w-full px-4 py-3 bg-white/5 border border-purple-300/20 "
                "rounded-xl text-white "
                "focus:ring-2 focus:ring-purple-400"
            ),
        }),
    )
    use_4bit = forms.BooleanField(
        initial=True,
        required=False,
        widget=forms.CheckboxInput(attrs={
            "class": (
                "h-4 w-4 text-purple-600 bg-white/5 "
                "border-purple-300/20 rounded focus:ring-purple-400"
            ),
        }),
    )
