#
# env.sh
#

ENV_SCRIPT_DIR=$(dirname ${0})

CWD=$(pwd)

cd ${ENV_SCRIPT_DIR}

uv venv
uv sync --all-groups --all-extras --all-packages --dev

source .venv/bin/activate deactivate
source .venv/bin/activate

export PATH=$(pwd):${PATH}
export PYTHONPATH=$(pwd)/src:$(pwd)

cd ${CWD}
