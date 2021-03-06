"""
This module contains helper methods to help schedule tasks on TaskCluster
which will use the buildbot-bridge system to trigger them on buildbot.
"""
from __future__ import absolute_import

from mozci.errors import MozciError
from mozci.mozci import valid_builder
from mozci.platforms import get_buildername_metadata
from mozci.sources.buildapi import query_repo_url
from mozci.sources.pushlog import query_revision_info
from mozci.sources.tc import (
    get_task,
    get_task_graph_status,
    create_task,
    generate_task_graph,
    schedule_graph,
    extend_task_graph,
)


def _create_task(buildername, repo_name, revision, task_graph_id=None,
                 parent_task_id=None, requires=None):
    """Return takcluster task to trigger a buildbot builder.

    This function creates a generic task with the minimum amount of
    information required for the buildbot-bridge to consider it valid.
    You can establish a list dependencies to other tasks through the requires
    field.

    :param buildername: The name of a buildbot builder.
    :type buildername: str
    :param repo_name: The name of a repository e.g. mozilla-inbound
    :type repo_name: str
    :param revision: Changeset ID of a revision.
    :type revision: str
    :param task_graph_id: TC graph id to which this task belongs to
    :type task_graph_id: str
    :param parent_task_id: Task from which to find artifacts. It is not a dependency.
    :type parent_task_id: str
    :param requires: List of taskIds of other tasks which this task depends on.
    :type requires: list
    :returns: TaskCluster graph
    :rtype: dict

    """
    if not valid_builder(buildername):
        raise MozciError("The builder '%s' is not a valid one." % buildername)

    builder_info = get_buildername_metadata(buildername)
    if builder_info['repo_name'] != repo_name:
        raise MozciError(
            "The builder '%s' should be for repo: %s." % (buildername, repo_name)
        )

    repo_url = query_repo_url(repo_name)
    push_info = query_revision_info(repo_url, revision)

    # XXX: We should validate that the parent task is a valid parent platform
    #      e.g. do not schedule Windows tests against Linux builds
    task = create_task(
        repo_name=repo_name,
        revision=revision,
        taskGroupId=task_graph_id,
        workerType='buildbot-bridge',
        provisionerId='buildbot-bridge',
        payload={
            'buildername': buildername,
            'sourcestamp': {
                'branch': repo_name,
                'revision': revision
            },
            # Needed because of bug 1195751
            'properties': {
                'product': builder_info['product'],
                'who': push_info['user']
            }
        },
        metadata_name=buildername
    )

    if requires:
        task['requires'] = requires

    # Setting a parent_task_id as a property allows Mozharness to
    # determine the artifacts we need for this job to run properly
    if parent_task_id:
        task['task']['payload']['properties']['parent_task_id'] = parent_task_id

    return task


def buildbot_graph_builder(builders):
    """ Return graph of builders based on a list of builders.

    # XXX: It would be better if had a BuildbotGraph class instead of messing
           with dictionaries.
           https://github.com/armenzg/mozilla_ci_tools/issues/353

    Input: ['BuilderA', 'BuilderB']
    Output: {'BuilderA': None, 'BuilderB'" None}

    Graph of N levels:
        {
           'Builder a1': {
               'Builder a2': {
                   ...
                       'Builder aN': None
               },
           },
           'Builder b1': None
        }

    :param builders: List of builder names
    :type builders: list
    :return: A graph of buildernames (single level of graphs)
    :rtype: dict

    """
    graph = {}
    for b in builders:
        graph[b] = None

    return graph


def generate_graph_from_builders(repo_name, revision, buildernames, *args, **kwargs):
    """Return TaskCluster graph based on a list of buildernames.

    :param repo_name The name of a repository e.g. mozilla-inbound
    :type repo_name: str
    :param revision: push revision
    :type revision: str
    :param buildernames: List of Buildbot buildernames
    :type revision: list

    :returns: return None or a valid taskcluster task graph.
    :rtype: dict

    """
    return generate_builders_tc_graph(
        repo_name=repo_name,
        revision=revision,
        builders_graph=buildbot_graph_builder(buildernames))


def generate_builders_tc_graph(repo_name, revision, builders_graph):
    """Return TaskCluster graph based on builders_graph.

    NOTE: We currently only support depending on one single parent.

    :param repo_name The name of a repository e.g. mozilla-inbound
    :type repo_name: str
    :param revision: push revision
    :type revision: str
    :param builders_graph:
        It is a graph made up of a dictionary where each
        key is a Buildbot buildername. The value for each key is either None
        or another graph of dependent builders.
    :type builders_graph: dict
    :returns: return None or a valid taskcluster task graph.
    :rtype: dict

    """
    if builders_graph is None:
        return None

    # This is the initial task graph which we're defining
    task_graph = generate_task_graph(
        repo_name=repo_name,
        revision=revision,
        scopes=[
            # This is needed to define tasks which take advantage of the BBB
            'queue:define-task:buildbot-bridge/buildbot-bridge',
        ],
        tasks=_generate_tasks(
            repo_name=repo_name,
            revision=revision,
            builders_graph=builders_graph
        )
    )

    return task_graph


def _generate_tasks(repo_name, revision, builders_graph, task_graph_id=None,
                    parent_task_id=None, required_task_ids=[], **kwargs):
    """ Generate a TC json object with tasks based on a graph of graphs of buildernames

    :param repo_name: The name of a repository e.g. mozilla-inbound
    :type repo_name: str
    :param revision: Changeset ID of a revision.
    :type revision: str
    :param builders_graph:
        It is a graph made up of a dictionary where each
        key is a Buildbot buildername. The value for each key is either None
        or another graph of dependent builders.
    :type builders_graph: dict
    :param task_graph_id: TC graph id to which this task belongs to
    :type task_graph_id: str
    :param parent_task_id: Task from which to find artifacts. It is not a dependency.
    :type parent_task_id: int
    :returns: A dictionary of TC tasks
    :rtype: dict

    """
    if not type(required_task_ids) == list:
        raise MozciError("required_task_ids must be a list")

    tasks = []

    if type(builders_graph) != dict:
        raise MozciError("The buildbot graph should be a dictionary")

    # Let's iterate through the upstream builders
    for builder, dependent_graph in builders_graph.iteritems():
        task = _create_task(
            buildername=builder,
            repo_name=repo_name,
            revision=revision,
            task_graph_id=task_graph_id,
            parent_task_id=parent_task_id,
            requires=required_task_ids,
            **kwargs
        )
        task_id = task['taskId']
        tasks.append(task)

        if dependent_graph:
            # If there are builders this builder triggers let's add them as well
            tasks = tasks + _generate_tasks(
                repo_name=repo_name,
                revision=revision,
                builders_graph=dependent_graph,
                task_graph_id=task_graph_id,
                required_task_ids=[task_id],
                **kwargs
            )

    return tasks


def trigger_builders_based_on_task_id(repo_name, revision, task_id, builders,
                                      *args, **kwargs):
    """ Create a graph of tasks which will use a TC task as their parent task.

    :param repo_name The name of a repository e.g. mozilla-inbound
    :type repo_name: str
    :param revision: push revision
    :type revision: str
    :returns: Result of scheduling a TC graph
    :rtype: dict

    """
    if not builders:
        return None

    if type(builders) != list:
        raise MozciError("builders must be a list")

    # If the task_id is of a task which is running we want to extend the graph
    # instead of submitting an independent one
    task = get_task(task_id)
    task_graph_id = task['taskGroupId']
    state = get_task_graph_status(task_graph_id)
    builders_graph = buildbot_graph_builder(builders)

    if state == "running":
        required_task_ids = [task_id]
    else:
        required_task_ids = []

    task_graph = generate_task_graph(
        repo_name=repo_name,
        revision=revision,
        scopes=[
            # This is needed to define tasks which take advantage of the BBB
            'queue:define-task:buildbot-bridge/buildbot-bridge',
        ],
        tasks=_generate_tasks(
            repo_name=repo_name,
            revision=revision,
            builders_graph=builders_graph,
            # This points to which parent to grab artifacts from
            parent_task_id=task_id,
            # This creates dependencies on other tasks
            required_task_ids=required_task_ids,
        )
    )

    if state == "running":
        result = extend_task_graph(task_graph_id, task_graph)
    else:
        result = schedule_graph(task_graph, *args, **kwargs)

    print result
    return result
