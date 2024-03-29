from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.generic import View
from django.http import Http404
from django.http import HttpResponse
from django.template import loader

import reports


class Report(View):
    report_template = 'reports/report.html'
    listing_template = 'reports/list.html'

    def get(self, request, name=None):
        #user = User.objects.get(username=request.user.username)
        # if not user.is_staff:
        #    return HttpResponseRedirect(reverse('login'))

        if name is None:
            results = reports.listing()
            t = loader.get_template(self.listing_template)
            html = t.render({'reports': results}, request)
        else:
            # display the requested report or 404
            try:
                results = reports.run(name)
                t = loader.get_template(self.report_template)
                html = t.render({'report_name': reports.display_name(name),
                                 'report': results}, request)
            except NotImplementedError:
                raise Http404("Report {0} not found".format(name))

        return HttpResponse(html)
