import requests
import urlparse
import urllib
from django.conf import settings
from django.core.management.base import BaseCommand
from tools.models import Tool, ToolData


class Command(BaseCommand):

    help = 'Import Galaxy tools to NGPhylogeny'

    def handle(self, *args, **options):

        self.handle_noargs()

    def handle_noargs(self):

        params = urllib.urlencode({'q': "phylogeny"}, True)
        url = '%s/%s/%s/?%s' % (settings.GALAXY_SERVER_URL, 'api', 'tools', params)
        connection = requests.get(url)
        tools_ids = []
        if connection.status_code == 200:
            tools_ids = connection.json()

        for id_tool in tools_ids:
            params = urllib.urlencode({'io_details': "true"}, True)
            tool_url = '%s/%s/%s/%s/?%s' % (settings.GALAXY_SERVER_URL, 'api', 'tools', id_tool, params )
            tool_info_request = requests.get(tool_url)
            tool_info = tool_info_request.json()

            tool_name = tool_info.get('name')
            inputs_tools = tool_info.get('inputs')

            t, created = Tool.objects.get_or_create(id_galaxy=id_tool)

            if created:
                t.name = tool_name
                t.save()
                for input_d in inputs_tools:

                    if input_d.get('type') == 'data':
                        input_obj = ToolData(name=input_d.get('name'),
                                             edam_formats=input_d.get('edam_formats'),
                                             extensions=input_d.get('extensions'),
                                             type='i',
                                             tool=t
                                             )
                        input_obj.save()
                        print "inputs:", input_d.get('name'), input_d.get('extensions'), input_d.get('edam_formats')

                outputs_tools = tool_info.get('outputs')

                for output_d in outputs_tools:
                    output_obj = ToolData(name=output_d.get('name'),
                                          edam_formats=output_d.get('edam_format'),
                                          extensions=output_d.get('format'),
                                          type='o',
                                          tool=t
                                          )
                    output_obj.save()
                    print "output", output_d.get('name'), output_d.get('format'), output_d.get('edam_format')


