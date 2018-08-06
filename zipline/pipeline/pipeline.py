import six

from zipline.errors import UnsupportedPipelineOutput
from zipline.utils.input_validation import (
    expect_element,
    expect_types,
    optional,
)

from .domain import Domain, infer_domain
from .graph import ExecutionPlan, TermGraph, _SCREEN_NAME
from .filters import Filter
from .sentinels import NotSpecified, NotSpecifiedType
from .term import AssetExists, ComputableTerm, Term


class Pipeline(object):
    """
    A Pipeline object represents a collection of named expressions to be
    compiled and executed by a PipelineEngine.

    A Pipeline has two important attributes: 'columns', a dictionary of named
    `Term` instances, and 'screen', a Filter representing criteria for
    including an asset in the results of a Pipeline.

    To compute a pipeline in the context of a TradingAlgorithm, users must call
    ``attach_pipeline`` in their ``initialize`` function to register that the
    pipeline should be computed each trading day.  The outputs of a pipeline on
    a given day can be accessed by calling ``pipeline_output`` in
    ``handle_data`` or ``before_trading_start``.

    Parameters
    ----------
    columns : dict, optional
        Initial columns.
    screen : zipline.pipeline.term.Filter, optional
        Initial screen.
    """
    __slots__ = ('_columns', '_screen', '_domain', '__weakref__')

    # TODO_SS: It's a little weird that we're usiong None for two of these
    # sentinels and using NotSpecified for the other.
    @expect_types(
        columns=optional(dict),
        screen=optional(Filter),
        domain=(Domain, NotSpecifiedType),
    )
    def __init__(self, columns=None, screen=None, domain=NotSpecified):
        if columns is None:
            columns = {}

        validate_column = self.validate_column
        for column_name, term in columns.items():
            validate_column(column_name, term)
            if not isinstance(term, ComputableTerm):
                raise TypeError(
                    "Column {column_name!r} contains an invalid pipeline term "
                    "({term}). Did you mean to append '.latest'?".format(
                        column_name=column_name, term=term,
                    )
                )

        self._columns = columns
        self._screen = screen
        self._domain = domain

    @property
    def columns(self):
        """
        The columns registered with this pipeline.
        """
        return self._columns

    @property
    def screen(self):
        """
        The screen applied to the rows of this pipeline.
        """
        return self._screen

    @expect_types(term=Term, name=str)
    def add(self, term, name, overwrite=False):
        """
        Add a column.

        The results of computing `term` will show up as a column in the
        DataFrame produced by running this pipeline.

        Parameters
        ----------
        column : zipline.pipeline.Term
            A Filter, Factor, or Classifier to add to the pipeline.
        name : str
            Name of the column to add.
        overwrite : bool
            Whether to overwrite the existing entry if we already have a column
            named `name`.
        """
        self.validate_column(name, term)

        columns = self.columns
        if name in columns:
            if overwrite:
                self.remove(name)
            else:
                raise KeyError("Column '{}' already exists.".format(name))

        if not isinstance(term, ComputableTerm):
            raise TypeError(
                "{term} is not a valid pipeline column. Did you mean to "
                "append '.latest'?".format(term=term)
            )

        self._columns[name] = term

    @expect_types(name=str)
    def remove(self, name):
        """
        Remove a column.

        Parameters
        ----------
        name : str
            The name of the column to remove.

        Raises
        ------
        KeyError
            If `name` is not in self.columns.

        Returns
        -------
        removed : zipline.pipeline.term.Term
            The removed term.
        """
        return self.columns.pop(name)

    @expect_types(screen=Filter, overwrite=(bool, int))
    def set_screen(self, screen, overwrite=False):
        """
        Set a screen on this Pipeline.

        Parameters
        ----------
        filter : zipline.pipeline.Filter
            The filter to apply as a screen.
        overwrite : bool
            Whether to overwrite any existing screen.  If overwrite is False
            and self.screen is not None, we raise an error.
        """
        if self._screen is not None and not overwrite:
            raise ValueError(
                "set_screen() called with overwrite=False and screen already "
                "set.\n"
                "If you want to apply multiple filters as a screen use "
                "set_screen(filter1 & filter2 & ...).\n"
                "If you want to replace the previous screen with a new one, "
                "use set_screen(new_filter, overwrite=True)."
            )
        self._screen = screen

    def to_execution_plan(self,
                          default_screen,
                          all_dates,
                          start_date,
                          end_date):
        """
        Compile into an ExecutionPlan.

        Parameters
        ----------
        default_screen : zipline.pipeline.term.Term
            Term to use as a screen if self.screen is None.
        all_dates : pd.DatetimeIndex
            A calendar of dates to use to calculate starts and ends for each
            term.
        start_date : pd.Timestamp
            The first date of requested output.
        end_date : pd.Timestamp
            The last date of requested output.

        Returns
        -------
        graph : zipline.pipeline.graph.ExecutionPlan
            Graph encoding term dependencies, including metadata about extra
            row requirements.
        """
        return ExecutionPlan(
            self._prepare_graph_terms(default_screen),
            all_dates,
            start_date,
            end_date,
        )

    def to_simple_graph(self, default_screen):
        """
        Compile into a simple TermGraph with no extra row metadata.

        Parameters
        ----------
        default_screen : zipline.pipeline.term.Term
            Term to use as a screen if self.screen is None.

        Returns
        -------
        graph : zipline.pipeline.graph.TermGraph
            Graph encoding term dependencies.
        """
        return TermGraph(self._prepare_graph_terms(default_screen))

    def _prepare_graph_terms(self, default_screen):
        """Helper for to_graph and to_execution_plan."""
        columns = self.columns.copy()
        screen = self.screen
        if screen is None:
            screen = default_screen
        columns[_SCREEN_NAME] = screen
        return columns

    @expect_element(format=('svg', 'png', 'jpeg'))
    def show_graph(self, format='svg'):
        """
        Render this Pipeline as a DAG.

        Parameters
        ----------
        format : {'svg', 'png', 'jpeg'}
            Image format to render with.  Default is 'svg'.
        """
        g = self.to_simple_graph(AssetExists())
        if format == 'svg':
            return g.svg
        elif format == 'png':
            return g.png
        elif format == 'jpeg':
            return g.jpeg
        else:
            # We should never get here because of the expect_element decorator
            # above.
            raise AssertionError("Unknown graph format %r." % format)

    @staticmethod
    @expect_types(term=Term, column_name=six.string_types)
    def validate_column(column_name, term):
        if term.ndim == 1:
            raise UnsupportedPipelineOutput(column_name=column_name, term=term)

    @expect_types(default=(Domain, NotSpecifiedType))
    def domain(self, default):
        """
        Get the domain for this pipeline.

        If an explicit domain was provided at construction time, return it.

        Otherwise, infer a domain from the registered columns.

        Parameters
        ----------
        default : zipline.pipeline.Domain or NotSpecified

        Returns
        -------
        domain : zipline.pipeline.Domain or NotSpecified
            The domain for the pipeline, or NotSpecified if no domain was
            provided and none can be inferred.
        """
        inferred = infer_domain(self.columns.values())

        if inferred is NotSpecified:
            # TODO_SS: Whose job should it be to barf on a NotSpecified domain?
            return self._domain
        elif self._domain is NotSpecified or self._domain == inferred:
            return inferred

        # We inferred a concrete domain that doesn't match the concrete
        # domain passed by the user. Barf.
        raise ValueError(
            "Conflicting domains in Pipeline. Inferred {}, but {} was "
            "passed at construction.".format(inferred, self._domain)
        )
