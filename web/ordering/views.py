'''
Purpose: Define the espa-web ordering view logic
Author: David V. Hill
'''

import json
import collections
import logging

from django import forms
from django.db import connection
from django.contrib.syndication.views import Feed
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.http import Http404
from django.shortcuts import redirect
from django.template import loader
from django.template import RequestContext
from django.template.context_processors import csrf
from django.utils.feedgenerator import Rss201rev2Feed
from django.views.generic import View
import django.contrib.auth
from django.contrib.auth.models import User

from ordering import emails
from ordering import sensor
from ordering import utilities
from ordering import validators
from ordering.models.order import Order
from ordering.models.configuration import Configuration as config

logger = logging.getLogger(__name__)

class AbstractView(View):

    def _display_system_message(self, ctx):
        '''Utility method to populate the context with systems messages if
        there are any configured for display

        Keyword args:
        ctx -- A RequestContext object (dictionary)

        Return:
        No return.  Dictionary is passed by reference.
        '''

        system_msg_enabled = config.get('system.display_system_message')

        # system message is only going to be displayed if the msg.lower() is
        # equal to 'true' (string value)
        if system_msg_enabled.lower() == 'true':
            title = config.get('msg.system_message_title')
            body = config.get('msg.system_message_body')
            
            ctx['display_system_message'] = True
            ctx['system_message_title'] = title
            ctx['system_message_body'] = body
        else:
            ctx['display_system_message'] = False

        return ctx

    def _get_request_context(self,
                             request,
                             params=None,
                             include_system_message=True):

        #context = RequestContext(request, params)
        if params is None:
            params = {}

        #make sure theres always a csrf token
        params.update(csrf(request))

        if include_system_message == True:
            return self._display_system_message(params)
        else:
            return params


class AjaxForm(AbstractView):
    def get(self, request):
        template = 'ordering/test.html'
        c = self._get_request_context(request)
        t = loader.get_template(template)
        return HttpResponse(t.render(c))


class TestAjax(AbstractView):

    def render_to_json_response(self, context, **response_kwargs):
        data = json.dumps(context)
        response_kwargs['content_type'] = 'application/json'
        return HttpResponse(data, **response_kwargs)

    def get(self, request):

        name = request.GET.get('name', '')

        data = {'user': request.user.get_username(),
                'name': name,
                'status': 'GET request ok'}

        return self.render_to_json_response(data)

    def post(self, request):

        name = "No name provided"
        if 'name' in request.POST:
            name = request.POST['name']

        age = "No age provided"
        if 'age' in request.POST:
            age = request.POST['age']

        data = {'user': request.user.get_username(),
                'name': name,
                'age': age,
                'status': 'POST request ok'}

        return self.render_to_json_response(data)


class Index(AbstractView):

    def get(self, request):
        '''Request handler for / and /index

        Keyword args:
        request -- HTTP request object

        Return:
        HttpResponse
        '''
        logger.info('{0} hit the index page'.format(request.user))
        c = self._get_request_context(request)
        t = loader.get_template('ordering/index.html')
        return HttpResponse(t.render(c, request))


class NewOrder(AbstractView):
    template = 'ordering/new_order.html'
    input_product_list = None

    def _get_order_description(self, parameters):
        description = None
        if 'order_description' in parameters:
            description = parameters['order_description']
        return description

    def _get_order_options(self, request):

        defaults = Order.get_default_options()

        # This will make sure no additional options past the ones we are
        # expecting will make it into the database
        #for key in request.POST.iterkeys():
        for key in defaults:
            if key in request.POST.iterkeys():
                val = request.POST[key]
                if val is True or str(val).lower() == 'on':
                    defaults[key] = True
                elif utilities.is_number(val):
                    if str(val).find('.') != -1:
                        defaults[key] = float(val)
                    else:
                        defaults[key] = int(val)
                else:
                    defaults[key] = val

        return defaults

    def _get_input_product_list(self, request):

        if not self.input_product_list:
            if 'input_product_list' in request.FILES:
                _ipl_str = request.FILES['input_product_list'].read()
                _splitter = "\n" if "\n" in _ipl_str else "\r"
                _ipl = _ipl_str.split(_splitter)
                self.input_product_list = _ipl

        retval = collections.namedtuple("InputProductListResult",
                                        ['input_products', 'not_implemented'])
        retval.input_products = list()
        retval.not_implemented = list()

        if self.input_product_list:
            for line in self.input_product_list:

                line = line.strip()

                try:
                    s = sensor.instance(line)
                    retval.input_products.append(s)
                except sensor.ProductNotImplemented, ni:
                    retval.not_implemented.append(ni.product_id)

        return retval

    def _get_verified_input_product_list(self, request):

        ipl = self._get_input_product_list(request)

        if ipl:
            payload = {'input_products': ipl.input_products}

            lplv = validators.LandsatProductListValidator(payload)

            mplv = validators.ModisProductListValidator(payload)

            landsat = lplv.get_verified_input_product_set(ipl.input_products)

            modis = mplv.get_verified_input_product_set(ipl.input_products)

            return list(landsat.union(modis))
        else:
            return None

    def get(self, request):
        '''Request handler for new order initial form

        Keyword args:
        request -- HTTP request object

        Return:
        HttpResponse
        '''

        c = self._get_request_context(request)
        c['user'] = request.user
        #c['optionstyle'] = self._get_option_style(request)

        t = loader.get_template(self.template)
        html = t.render(c, request)
        logger.info('Rendering html from request:{0}'.format(request))
        logger.info('... with context:{0}'.format(c))
        #logger.info('... rendered html:{0}'.format(html))
        return HttpResponse(html)

    def post(self, request):
        '''Request handler for new order submission

        Keyword args:
        request -- HTTP request object

        Return:
        HttpResponseRedirect upon successful submission
        HttpResponse if there are errors in the submission
        '''
        #request must be a POST and must also be encoded as multipart/form-data
        #in order for the files to be uploaded

        validator_parameters = {}

        # coerce the request.POST to be a normal Python dictionary
        validator_parameters = dict(request.POST)

        # retrieve the namedtuple for the input product list
        ipl = self._get_input_product_list(request)

        # send the validator only the items in the list that could actually
        # be instantiated as a sensor.  The other tuple item not_implemented
        # is being ignored unless we want to tell the users about all the
        # junk they included in their input file
        validator_parameters['input_products'] = ipl.input_products

        validator = validators.NewOrderValidator(validator_parameters)

        validation_errors = validator.errors()

        #if validator.errors():
        if validation_errors:

            c = self._get_request_context(request)

            #unwind the validator errors.  It comes out as a dict with a key
            #for the input field name and a value of a list of error messages.
            # At this point we are only displaying the error messages in one
            # block but going forward will be able to put the error message
            # right next to the field where the error occurred once the
            # template is properly modified.
            errors = validation_errors.values()

            error_list = list()

            for e in errors:
                for m in e:
                    m = m.replace("\n", "<br/>")
                    m = m.replace("\t", "    &#149; ")
                    m = m.replace(" ", "&nbsp;")
                    error_list.append(m)

            c['errors'] = sorted(error_list)
            c['user'] = request.user

            t = loader.get_template(self.template)

            return HttpResponse(t.render(c))

        else:

            vipl = self._get_verified_input_product_list(request)

            order_options = self._get_order_options(request)

            order_type = "level2_ondemand"

            if order_options['include_statistics'] is True:
                vipl.append("plot")
                order_type = "lpcs"

            option_string = json.dumps(order_options,
                                       sort_keys=True,
                                       indent=4)

            desc = self._get_order_description(request.POST)

            order = Order.enter_new_order(request.user.username,
                                          'espa',
                                          vipl,
                                          option_string,
                                          order_type,
                                          note=desc
                                          )

            email = order.user.email

            url = reverse('list_orders', kwargs={'email': email})

            return HttpResponseRedirect(url)


class ListOrders(AbstractView):
    template = "ordering/listorders.html"

    def get(self, request, email=None, output_format=None):
        '''Request handler for displaying all user orders

        Keyword args:
        request -- HTTP request object
        email -- the user's email
        output_format -- deprecated

        Return:
        HttpResponse
        '''

        if email is None or not emails.Emails().validate_email(email):
            user = User.objects.get(username=request.user.username)
            email = user.email

        orders = Order.list_all_orders(email)

        form = ListOrdersForm(initial={'email': email})

        c = self._get_request_context(request, {'form': form,
                                                'email': email,
                                                'orders': orders
                                                })

        t = loader.get_template(self.template)

        return HttpResponse(t.render(c, request))


class OrderDetails(AbstractView):
    template = 'ordering/orderdetails.html'

    def get(self, request, orderid, output_format=None):
        '''Request handler to get the full listing of all the scenes
        & statuses for an order

        Keyword args:
        request -- HTTP request object
        orderid -- the order id for the order
        output_format -- deprecated

        Return:
        HttpResponse
        '''

        t = loader.get_template(self.template)
        try:
            orders, scenes = Order.get_order_details(orderid)
            logger.info("Found {0} scenes for order {1}".format(len(scenes), orders.orderid))
            html = t.render({'order':orders, 'scenes':scenes}, request)
            return HttpResponse(html)
        except Order.DoesNotExist:
            raise Http404


class LogOut(AbstractView):
    template = "ordering/loggedout.html"

    def get(self, request):
        '''Simple view to log a user out and land them on an exit page'''

        django.contrib.auth.logout(request)

        t = loader.get_template(self.template)

        c = self._get_request_context(request, include_system_message=False)

        #return HttpResponse(t.render(c, request))
        return redirect('login')


class ListOrdersForm(forms.Form):
    '''Form object for the ListOrders form'''
    email = forms.EmailField()


class StatusFeed(Feed):
    '''Feed subclass to publish user orders via RSS'''

    feed_type = Rss201rev2Feed

    title = "ESPA Status Feed"

    link = ""

    email = ""

    def get_object(self, request, email):
        self.email = email

        query = ("select u.email, o.orderid, o.order_date, p.name,"
                 "p.product_dload_url, p.status "
                 "from auth_user u, ordering_order o, ordering_scene p "
                 "where u.id = o.user_id and o.id = p.order_id "
                 "and p.status = 'complete' and u.email = %s and "
                 "o.status != 'purged'")

        results = None

        cursor = connection.cursor()

        if cursor is not None:
            try:
                cursor.execute(query, [email])
                results = utilities.dictfetchall(cursor)
            finally:
                if cursor is not None:
                    cursor.close()

        if not results or len(results) == 0:
            raise Http404
        else:
            return results

    def items(self, results):
        return results

    def link(self, results):
        return reverse('status_feed',
                       kwargs={'email': results[0]['email']})

    def description(self, results):
        return "ESPA scene status for:%s" % results[0]['email']

    def item_title(self, result):
        return result['name']

    def item_link(self, result):
        return result['product_dload_url']

    def item_description(self, result):
        return "scene_status:%s,orderid:%s,orderdate:%s" \
            % ('complete', result['orderid'], result['order_date'])

