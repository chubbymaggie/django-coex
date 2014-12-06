from django.db.models import Manager
from django.db.models.query import QuerySet

import fuzzy

class SymMixin():
    pass
    #def filter(self, *args, **kwargs):
        #print 'filter'
        #self.mutate()
        #actual = super(SymQuerySet, self).filter(args, kwargs)
        #self.is_mutable(kwargs)
        #return actual

    #def is_mutable(self, **kwargs):
        #for lookup_key, value in kwargs: 
            #print lookup_key, value

    #def mutate(self, *args, **kwargs):
        #clone = self.all()

class QueryMutation(object):
    pass

class SymQuerySet(QuerySet, SymMixin):
    operators = ['lte', 'gte', 'gt', 'lt', 'exact'];

    # TODO: merge with newget in symdjango
    #def get(self, *args, **kwargs):
        #if len(kwargs) == 1:
          #key = kwargs.keys()[0]
          #value = kwargs[value]

          #if '_' not in key and isinstance(value, concolic_str):
            #if key == 'pk':
              #key = self.model._meta.pk.name
              #kwargs[key] = kwargs['pk']
              #del kwargs['pk']

            #result = super(SymQuerySet, self).get(args, kwargs)

        #return super(SymQuerySet, self).get(args, kwargs)

    def filter(self, *args, **kwargs):
        #print 'filter'
        mutations = self._mutate(*args, **kwargs)
        actual = self._apply_filter(*args, **kwargs)
        mutations = self.remove_dead_mutations(actual, mutations)
        return actual

    def _apply_filter(self, *args, **kwargs):
        from django.core.exceptions import FieldError
        try:
            import pdb; pdb.set_trace()
            return super(SymQuerySet, self).filter(*args, **kwargs)
        except FieldError: 
            pass

    #
    # Based on ConSMutate: SQL mutants for guiding concolic testing of database 
    # applications (T. Sarkar, S. Basu, and J.S. Wong, 2012)
    #
    # Mutate the current queryset when it is filtered
    #
    # Suppose the filter is Transfer.objects.filter(zoobars__gt=10) (all 
    # transfers of more than 10 zoobars)
    #
    # 1. Split the input string: filter_column = zoobars, operator = gt,
    # filter_value = 10
    # 2. Create possible mutations:
    #   i. Create mutated querysets by varying the 'operator', e.g. create 
    #      querysets with operator = 'lt' (less than), 'gte' (greater than or
    #      equal), etc.
    #   ii.Should end up with several mutations: e.g. 
    #      Transfer.objects.filter(zoobars__lt=10), 
    #      Transfer.objects.filter(zoobars__gte=10), etc.
    # 3. Run original filter
    # 4. For each mutation: 
    #   i. Run it and compare with original
    #   ii.If result is the same (called 'dead' mutations in the paper): discard
    #   iii.If result is different: mutated condition explores different 'branch'
    #       of the DB. Add the symmetric difference of the original and the 
    #       mutation to the path constraints
    #
    def _mutate(self, *args, **kwargs):
        mutations = []
        for arg in kwargs:
            lookups, parts, reffed_aggregate = self.query.solve_lookup_type(arg)

            if len(lookups) != 1:
                continue

            mutated_filters = []
            operator = lookups[0]
            filter_column = '_'.join(parts)
            filter_value = kwargs[arg]

            mutate_operators = [op for op in self.operators if op != operator]
            for op in mutate_operators:
                mutated_filters.append({filter_column + '__' + op: filter_value})

            # TODO: currently only handles filters with single column queries
            # e.g. username='alice'. Ideally, this would handle filters over
            # multiple columns e.g. find the transfers of more than 10 zoobars 
            # to alice recipient='alice' && zoobars > 10
            #break
            return self._create_mutated_querysets(mutated_filters, *args) 

            #mutations.append(mutation_set)

        return mutations

    def _create_mutated_querysets(self, mutated_filters, *args):
        mutations = []
        for filter_kv in mutated_filters:
            mutated_queryset = self._apply_filter(*args, **filter_kv)
            mutations.append(mutated_queryset)
        return mutations

    def remove_dead_mutations(self, original_queryset, mutations):
        unique_mutations = [m for m in mutations if original_queryset != m]
        return unique_mutations

class SymManager(Manager, SymMixin):
    def __init__(self, manager):
        self.manager = manager

    def __getattr__(self, attr):
        #print 'getattr' + attr
        return getattr(self.manager, attr)

    def get_queryset(self):
        #print 'symquery'
        return SymQuerySet(self.model, using=self._db, hints=self._hints)


