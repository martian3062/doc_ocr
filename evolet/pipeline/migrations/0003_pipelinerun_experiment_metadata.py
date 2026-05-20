from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("pipeline", "0002_finalrecord_relation_graph_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelinerun",
            name="run_kind",
            field=models.CharField(
                choices=[("main", "Main pipeline"), ("experiment", "Experimental pipeline")],
                default="main",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="pipelinerun",
            name="approach_key",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="pipelinerun",
            name="approach_config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="pipelinerun",
            name="comparison_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="pipelinerun",
            name="parent_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="experiment_runs",
                to="pipeline.pipelinerun",
            ),
        ),
    ]
