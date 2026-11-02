"""Report company scope: ``Profile.report_all_companies`` / ``report_company_scope`` / ``report_companies`` (the user's default)
and ``company_scope`` / ``scope_companies`` on saved reports and saved queries (blank = the user's setting). Defaults keep every
existing user and report on the active company, exactly as before."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0034_company_independent_reports_mail'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='report_all_companies',
            field=models.BooleanField(default=False, help_text='Off: reports and exports use the active company only.', verbose_name='report shows all companies'),
        ),
        migrations.AddField(
            model_name='profile',
            name='report_companies',
            field=models.ManyToManyField(blank=True, help_text='Used when the report companies are “Chosen companies”.', related_name='+', to='core.company', verbose_name='chosen report companies'),
        ),
        migrations.AddField(
            model_name='profile',
            name='report_company_scope',
            field=models.CharField(choices=[('active', 'Active company'), ('all', 'All my companies'), ('chosen', 'Chosen companies')], default='active', help_text='Which companies reports and exports cover by default (each run can change it).', max_length=10, verbose_name='report companies'),
        ),
        migrations.AddField(
            model_name='queryreport',
            name='company_scope',
            field=models.CharField(blank=True, choices=[('', "The user's own setting"), ('active', 'Active company'), ('all', 'All my companies'), ('chosen', 'Chosen companies')], default='', help_text="Default company scope when it is run (blank = the user's own setting).", max_length=10, verbose_name='companies'),
        ),
        migrations.AddField(
            model_name='queryreport',
            name='scope_companies',
            field=models.ManyToManyField(blank=True, related_name='+', to='core.company', verbose_name='chosen companies'),
        ),
        migrations.AddField(
            model_name='reportdefinition',
            name='company_scope',
            field=models.CharField(blank=True, choices=[('', "The user's own setting"), ('active', 'Active company'), ('all', 'All my companies'), ('chosen', 'Chosen companies')], default='', help_text="Default company scope when it is run (blank = the user's own setting).", max_length=10, verbose_name='companies'),
        ),
        migrations.AddField(
            model_name='reportdefinition',
            name='scope_companies',
            field=models.ManyToManyField(blank=True, related_name='+', to='core.company', verbose_name='chosen companies'),
        ),
    ]
