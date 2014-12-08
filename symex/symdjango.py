#!/usr/bin/env python2

import sys
import os
import fuzzy

# patch Django where needed
from mock import patch

# Dynamic imports
import importlib

# use our Django (currently irrelevant)
ourdjango = os.path.dirname(os.path.abspath(__file__)) + '/../../django-concolic'
if ourdjango not in sys.path:
  sys.path.insert(1, ourdjango)

# Mock out force_str and relatives
from django.utils.encoding import force_bytes
class NewForceBytes():
  def __call__(self, s, *args, **kwargs):
    if isinstance(s, fuzzy.concolic_str):
      return s
    if isinstance(s, fuzzy.concolic_int):
      return s
    return force_bytes(s, *args, **kwargs)

patcher = patch('django.utils.encoding.force_bytes', new_callable=NewForceBytes)
patcher.start()
patcher = patch('django.test.client.force_bytes', new_callable=NewForceBytes)
patcher.start()
# END

# Preserve symbolic values across POST data serialization (gah..)
# First, we do a bit of a trick when asked to create POST data by replacing
# concolic variables with a tagged key containing the symbolic identifier of
# the variable instead.
def post_data(**kwargs):
  data = {}

  tagged_key = lambda k: 'CoNcOlIc::' + type(k).__name__ + ':' + k._sym_ast().id
  for k in kwargs:
    v = kwargs[k]
    if type(v).__name__ in ("concolic_str", "concolic_int"):
      v = tagged_key(v)
    data[k] = v
  return data
# Then, we wrap django.http.MultiPartParser.parse so that it restores symbolic
# nature of tagged parts (look through self._post, first returned value).
from django.http.request import MultiPartParser
from django.http import QueryDict
class MPP(MultiPartParser):
  def parse(self):
    post, files = super(MPP, self).parse()
    newpost = QueryDict('', mutable=True)
    for k, vs in post.iterlists():
      if len(vs) == 1 and vs[0].startswith('CoNcOlIc::'):
        v = vs[0][len('CoNcOlIc::'):]
        ts = v.split(':', 2)
        if ts[0] == "concolic_int":
          vs = [fuzzy.mk_int(ts[1])]
        elif ts[0] == "concolic_str":
          vs = [fuzzy.mk_str(ts[1])]
        else:
          print("UNKNOWN CONCOLIC TYPE %s" % ts[0])
      newpost.setlist(k, vs)
    return newpost, files

patcher = patch('django.http.request.MultiPartParser', new=MPP)
patcher.start()
# There's also another type forcing happening in QueryDict that we need to
# override
from django.http.request import bytes_to_text
class NewBytes2Text():
  def __call__(self, s, encoding):
    if isinstance(s, fuzzy.concolic_str):
      return s
    if isinstance(s, fuzzy.concolic_int):
      return s
    return bytes_to_text(s, encoding)

patcher = patch('django.http.request.bytes_to_text', new_callable=NewBytes2Text)
patcher.start()
# END

# Mock DB queries so they play nicely with concolic execution
import django.db.models.query
from django.db.models import Model

def is_version_1_8():
  return django.VERSION[0] == 1 and django.VERSION[1] == 8

def is_version_1_6():
  return django.VERSION[0] == 1 and django.VERSION[1] == 6

notdict = {}

if is_version_1_8():
  qs = django.db.models.QuerySet 
else:
  qs = django.db.models.query.QuerySet


import symex.symqueryset
#qs.get = symex.symqueryset.AllSymQuerySet.get

import symex.importwrapper as importwrapper
import symex.rewriter as rewriter
importwrapper.rewrite_imports(rewriter.rewriter)

# It's only safe to use SymDjango as a singleton!
class SymDjango():
  def __init__(self, settings, path, viewmap):
    self.settings = settings
    self.path = path
    self.viewmap = viewmap

    # search for modules inside application under test
    sys.path.append(path)

    # Make sure Django reads the correct settings
    os.environ.update({
      "DJANGO_SETTINGS_MODULE": settings
    })
    django.setup()


  def setup_models(self):
    from django.db import models
    all_models = models.get_models()
    from symqueryset import SymManager, AllSymQuerySet

    from django.contrib.auth.models import User
    for m in all_models:
      #if not m._meta.model.__module__.startswith("django"):
      __objects = m._meta.model.objects
      m.objects = SymManager(__objects, AllSymQuerySet)
    __objects = User.objects
    User.objects = SymManager(__objects, AllSymQuerySet)


  def new(self):
    return SymClient(self, SERVER_NAME='concolic.io')

# Mock requests by mocking routing + url parsing
from django.test.client import Client

class SymClient(Client):
  def __init__(self, symdjango, **defaults):
    super(SymClient, self).__init__(False, **defaults)
    self.symdjango = symdjango

  def request(self, **request):
    with patch('django.core.urlresolvers.RegexURLResolver', new=SymResolver) as mock:
      mock.symdjango = self.symdjango
      return super(SymClient, self).request(**request)

  def generic(self, method, path, data='',
      content_type='application/octet-stream', secure=False, **extra):
    environ = self._base_environ(PATH_INFO=path, **extra)

    from urlparse import ParseResult
    with patch('django.test.client.urlparse') as mock:
      mock.return_value = ParseResult(
          scheme = environ['wsgi.url_scheme'],
          netloc = environ['SERVER_NAME'],
          path = environ['PATH_INFO'],
          params = '',
          query = 'QUERY_STRING' in environ and environ['QUERY_STRING'] or '',
          fragment = ''
          )
      return super(SymClient, self).generic(method, path, data,
          content_type=content_type, secure=secure, **extra)

class SymResolver():
  symdjango = None

  def __init__(self, regex, conf):
    self.reverseDict = {}
    for m in SymResolver.symdjango.viewmap:
      ind = m.find('.')
      self.reverseDict[m[:ind]] = ("", self)

  def resolve(self, path):
    from django.core.urlresolvers import Resolver404
    for v in SymResolver.symdjango.viewmap:
      s = SymURL(SymResolver.symdjango, v)
      r = s.resolve(path)
      if r is not None:
        return r

    raise Resolver404({'path': path})

  def _reverse_with_prefix(self, v, _prefix, *args, **kwargs):
    return "<reverse: %s>" % v

  @property
  def namespace_dict(self):
    return self.reverseDict

  @property
  def app_dict(self):
    return {}

class SymURL():
  def __init__(self, symdjango, v):
    self.symdjango = symdjango
    self.view = v

  @property
  def callback(self):
    return self.symdjango.viewmap[self.view]

  def resolve(self, path):
    from django.core.urlresolvers import ResolverMatch
    match = self.callback(path)
    if match:
      if not isinstance(match, tuple):
        match = (match, {}, [])
      if len(match) == 1:
        match = (match[0], {})
      if len(match) == 2:
        match = (match[0], match[1], [])

      # From core/urlresolvers.py (:222 in 1.7 stable):
      # If there are any named groups, use those as kwargs, ignoring non-named
      # groups. Otherwise, pass all non-named arguments as positional
      # arguments.
      kwargs = match[1]
      if kwargs:
        args = ()
      else:
        args = match[2]

      kwargs.update({}) # TODO: extra args passed to view from urls.py
      ind = self.view.rfind('.');
      mod = self.view[:ind]
      method = self.view[(ind+1):]
      views = importlib.import_module(mod);

      return ResolverMatch(getattr(views, method), args, kwargs, method)
