from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.shortcuts import render
from django.views.generic import View
from django.views.generic.detail import SingleObjectMixin
from django.core.files.uploadedfile import InMemoryUploadedFile

import tempfile

from data.views import UploadView
from galaxy.decorator import connection_galaxy
from tools.models import Tool

from workspace.views import create_history, delete_history

from workflows.forms import tool_form_factory
from workflows.views.generic import WorkflowWizard, WorkflowListView
from workflows.views.viewmixing import WorkflowDuplicateMixin
from workflows.views.viewmixing import WorkflowDetailMixin
from workspace.tasks import monitorworkspace
from workflows import WorkflowInputFileFormatError

from bioblend.galaxy.tools.inputs import inputs
from django.core.urlresolvers import reverse_lazy
from django.http import HttpResponseRedirect

from Bio import SeqIO

WORKFLOW_ADV_FLAG = "wadv"


def form_class_list(tools):
    """
    Create ToolForm classes on the fly to be used by WizardForm
    :param gi:
    :param tools:
    :return: list af Class form
    """
    tool_form_class = []
    for tool in tools:
        tool_form_class.append(tool_form_factory(tool))
    return tool_form_class


@method_decorator(connection_galaxy, name="dispatch")
class WorkflowAdvancedListView(WorkflowListView):
    """
        Workflow Advanced ListView
    """
    template_name = 'workflows/workflows_advanced_list.html'
    restricted_toolset = Tool.objects.filter(toolflag__name=WORKFLOW_ADV_FLAG)


class WorkflowAdvancedFormView(WorkflowDetailMixin, SingleObjectMixin):
    """
        Generic Workflow Advanced class-based view:
        - Cut selected workflow into multiple tool forms
    """
    # object = workflow

    template_name = 'workflows/workflows_advanced_form.html'
    context_object_name = "workflow"
    restricted_toolset = Tool.objects.filter(toolflag__name=WORKFLOW_ADV_FLAG)

    def get_context_data(self, **kwargs):

        if not self.object:
            self.object = self.get_object()
        self.fetch_workflow_detail(self.object)
        context = super(WorkflowAdvancedFormView,
                        self).get_context_data(**kwargs)
        context['workflow_list'] = [self.object, ]

        context['tool_list'] = []
        tools = []

        for t in self.object.detail:
            tools.append(t[1])
            context['tool_list'].append(slugify(t[1].name))

        context['form_list'] = form_class_list(tools)

        return context


@method_decorator(connection_galaxy, name="dispatch")
class WorkflowAdvancedSinglePageView(WorkflowDuplicateMixin,
                                     WorkflowAdvancedFormView,
                                     View):
    template_name = 'workflows/workflows_adv_singlepage_form.html'

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(object=self.object)
        return render(request, self.template_name, context)

    def process_file_to_upload(file_to_upload):
        uploadfile_name = ""
        if isinstance(file_to_upload, InMemoryUploadedFile):
            tmp_file = tempfile.NamedTemporaryFile()
            for chunk in file_to_upload.chunks():
                tmp_file.write(chunk)
            tmp_file.flush()
            uploadfile_name = str(file_to_upload.name)
        else:
            uploadfile_name = "Upload File"
            tmp_file = tempfile.NamedTemporaryFile()
            tmp_file.write(file_to_upload)
            tmp_file.flush()

            # Check that input file is Fasta and is not empty
        if not valid_fasta(tmp_file.name):
            raise WorkflowInputFileFormatError(
                "Input data is malformed or contain less than 4 sequences"
            )
        return tmp_file, uploadfile_name

    def post(self, request, *args, **kwargs):

        gi = request.galaxy

        workflow = self.get_workflow()
        context = self.get_context_data(object=self.object)
        # input file
        dataset_map = {}

        # tool params
        params = {}

        i_input = workflow.json['inputs'].keys()[0]
        steps = workflow.json['steps']
        step_id = u'0'

        # Handle workflow main input file
        # before creating the workspace etc.
        uploaded_file = request.FILES.get("file") or request.POST.get("file")

        # We check that a file has been given
        if not uploaded_file:
            context = self.get_context_data(object=self.object)
            context['fileerror'] = "No input file given"
            return render(request, self.template_name, context)
        
        # We check input file format
        try:
            tmp_file, uploadfile_name = self.process_file_to_upload(uploaded_file)
        except WorkflowInputFileFormatError as e:
            context = self.get_context_data(object=self.object)
            context['fileerror'] = str(e)
            return render(request, self.template_name, context)

        # We create an history (local and on galaxy)
        wksph = create_history(
            self.request, name="NGPhylogeny Analyse - " + workflow.name)
        
        # we send the file to galaxy
        output = gi.tools.upload_file(path=tmp_file.name,
                                      file_name=uploadfile_name,
                                      history_id=wksph.history)
        galaxy_file = output.get('outputs')[0].get('id')
        dataset_map[i_input] = {'id': galaxy_file, 'src': 'hda'}
        
        # We analyze submited forms
        
        for form in context['form_list']:
            tool_form = form(data=request.POST, prefix=form.prefix)
            if not tool_form.is_valid():
                # if form is not valid
                return self.get(request, *args, **kwargs)
            else:
                # print (tool_form.cleaned_data)
                tool_inputs = inputs()
                # mapping between form id to galaxy params names
                fields = tool_form.fields_ids_mapping
                inputs_data = set(tool_form.input_file_ids)
                # set the Galaxy parameter (name, value)
                for key, value in tool_form.cleaned_data.items():
                    if key not in inputs_data:
                        tool_inputs.set_param(fields.get(key), value)
                for inputfile in inputs_data:
                    uploaded_file = ""
                    if request.FILES:
                        uploaded_file = request.FILES.get(inputfile, '')
                    if uploaded_file:
                        tmp_file = tempfile.NamedTemporaryFile()
                        for chunk in uploaded_file.chunks():
                            tmp_file.write(chunk)
                        tmp_file.flush()
                        # send file to galaxy
                        outputs = gi.tools.upload_file(path=tmp_file.name,
                                                       file_name=uploaded_file.name,
                                                       history_id=wksph.history)
                        file_id = outputs.get('outputs')[0].get('id')
                        tool_inputs.set_dataset_param(
                            fields.get(inputfile), file_id)
                    else:
                        # else paste content
                        content = tool_form.cleaned_data.get(inputfile)
                        if content:
                            tmp_file = tempfile.NamedTemporaryFile()
                            tmp_file.write(content)
                            tmp_file.flush()
                            # send file to galaxy
                            input_fieldname = tool_form.fields_ids_mapping.get(
                                inputfile)
                            outputs = gi.tools.upload_file(path=tmp_file.name,
                                                           file_name=input_fieldname + " pasted_sequence",
                                                           history_id=wksph.history)
                            file_id = outputs.get('outputs')[0].get('id')
                            tool_inputs.set_dataset_param(fields.get(inputfile),
                                                          file_id)
                # workflow step
                # get from which step the tools are used
                for i, step in steps.items():
                    if getattr(tool_form, 'tool_id', 'null') == step.get('tool_id'):
                        step_id = i
                        break
                # convert inputs to dict
                params[step_id] = tool_inputs.to_dict()
                if not params[step_id]:
                    del params[step_id]
                print (params)

        # We run the galaxy workflow
        try:
            output = self.request.galaxy.workflows.invoke_workflow(
                workflow_id=workflow.id_galaxy,
                history_id=wksph.history,
                inputs=dataset_map,
                params=params,
                allow_tool_state_corrections=True,
            )

            self.succes_url = reverse_lazy("history_detail", kwargs={
                                           'history_id': wksph.history})
            # Start monitoring (for sending emails)
            monitorworkspace.delay(wksph.history)
            wksph.monitored = True
            wksph.save()

            return HttpResponseRedirect(self.succes_url)

        except Exception:
            delete_history(wksph.history)
            raise
        finally:
            # delete the workflow copy of oneclick workflow when
            # the workflow has been run
            print(self.clean_copy())


def valid_fasta(fasta_file):
    # Check uploaded file or pasted content
    nbseq = 0
    for r in SeqIO.parse(fasta_file, "fasta"):
        nbseq += 1
    return nbseq > 3
