# coding: utf8

import os
import sys
import json
import taskcluster


def main():
    build_task = create_task_with_built_image(
        "build task",
        "./build-task.sh",
        image="servo-x86_64-linux",

        artifacts=[
            ("executable.gz", "/repo/something-rust/something-rust.gz"),
        ],

        # https://docs.taskcluster.net/docs/reference/workers/docker-worker/docs/caches
        scopes=[
            "docker-worker:cache:cargo-registry-cache",
            "docker-worker:cache:cargo-git-cache",
        ],
        cache={
            "cargo-registry-cache": "/root/.cargo/registry",
            "cargo-git-cache": "/root/.cargo/git",
        },
    )

    create_task(
        "run task",
        "./run-task.sh",
        image="buildpack-deps:bionic-scm",
        dependencies=[build_task],
        env={"BUILD_TASK_ID": build_task},
    )


# https://hub.docker.com/r/servobrowser/image-builder/
# https://github.com/SimonSapin/servo-docker-image-builder-image
IMAGE_BUILDER_IMAGE = "servobrowser/image-builder@sha256:" \
    "f2370c4b28aa537e47c0cacb82cc53272233fa256b6634c0eebc46e2dd019333"

# https://docs.taskcluster.net/docs/reference/workers/docker-worker/docs/environment
DECISION_TASK_ID = os.environ["TASK_ID"]

# https://docs.taskcluster.net/docs/reference/workers/docker-worker/docs/features#feature-taskclusterproxy
QUEUE = taskcluster.Queue(options={"baseUrl": "http://taskcluster/queue/v1/"})

IMAGE_ARTIFACT_FILENAME = "image.tar.lz4"


def create_task_with_built_image(name, command, image, **kwargs):
    image_build_task = build_image(image)
    kwargs.setdefault("dependencies", []).append(image_build_task)
    image = {
        "type": "task-image",
        "taskId": image_build_task,
        "path": "public/" + IMAGE_ARTIFACT_FILENAME,
    }
    return create_task(name, command, image, **kwargs)


def build_image(name):
    image_build_task = create_task(
        "docker image build task for image: " + name,
        """
            docker build -t "$IMAGE" "docker/$IMAGE"
            docker save "$IMAGE" | lz4 > /%s
        """ % IMAGE_ARTIFACT_FILENAME,
        env={
            "IMAGE": name,
        },
        artifacts=[
            (IMAGE_ARTIFACT_FILENAME, "/" + IMAGE_ARTIFACT_FILENAME),
        ],
        image=IMAGE_BUILDER_IMAGE,
        features={
            "dind": True,  # docker-in-docker
        },
    )
    return image_build_task


def create_task(name, command, image, artifacts=None, dependencies=None, env=None, cache=None,
                scopes=None, features=None):
    env = env or {}
    for k in ["GITHUB_EVENT_CLONE_URL", "GITHUB_EVENT_COMMIT_SHA"]:
        env.setdefault(k, os.environ[k])

    task_id = taskcluster.slugId().decode("utf8")
    payload = {
        "taskGroupId": DECISION_TASK_ID,
        "dependencies": [DECISION_TASK_ID] + (dependencies or []),
        "schedulerId": "taskcluster-github",
        "provisionerId": "aws-provisioner-v1",
        "workerType": "servo-docker-worker",

        "created": taskcluster.fromNowJSON(""),
        "deadline": taskcluster.fromNowJSON("1 hour"),
        "metadata": {
            "name": "Taskcluster experiments for Servo: " + name,
            "description": "",
            "owner": os.environ["GITHUB_EVENT_OWNER"],
            "source": os.environ["GITHUB_EVENT_SOURCE"],
        },
        "scopes": scopes or [],
        "payload": {
            "cache": cache or {},
            "maxRunTime": 3600,
            "image": image,
            "command": [
                "/bin/bash",
                "--login",
                "-c",
                """
                    set -e
                    set -x
                    git clone $GITHUB_EVENT_CLONE_URL repo
                    cd repo
                    git checkout $GITHUB_EVENT_COMMIT_SHA
                """ + command
            ],
            "env": env,
            "artifacts": {
                "public/" + artifact_name: {
                    "type": "file",
                    "path": path,
                    "expires": taskcluster.fromNowJSON("1 week"),
                }
                for artifact_name, path in artifacts or []
            },
            "features": features or {},
        },
    }
    QUEUE.createTask(task_id, payload)
    print("Scheduled %s: %s" % (name, task_id))
    return task_id


if __name__ == "__main__":
    main()
