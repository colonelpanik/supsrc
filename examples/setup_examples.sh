#!/bin/sh

PWD=$(pwd)

repo_prefix=/tmp/supsrc-example-repo

for i in {1..3}; do (
    repo_dir=${repo_prefix}${i}
    echo "================================================================================"
    echo "🗄️ the repo dir is: ${repo_dir}"
    echo "================================================================================"

    echo "${repo_dir}: removing existing example repos."
    # this logic should actually check for repo and clear out * and .* instead of risky rm.
    rm -rf "${repo_dir}"

    echo "--------------------------------------------------------------------------------"
    echo "🎬 ${repo_dir}: init repo"
    echo "--------------------------------------------------------------------------------"
    mkdir -p ${repo_dir}
    git init ${repo_dir}

    echo "--------------------------------------------------------------------------------"
    echo "1️⃣ ${repo_dir}: create first commit"
    echo "--------------------------------------------------------------------------------"
    cd ${repo_dir}
    echo "# ${repo_dir}" > README.md
    git add README.md
    git commit -am "readme for ${repo_dir}"

    echo "--------------------------------------------------------------------------------"
    echo " ${repo_dir}: create fake origin"
    echo "--------------------------------------------------------------------------------"
    git init --bare ${repo_dir}/.git/origin
    git remote add origin ${repo_dir}/.git/origin
    git push --set-upstream origin main

    echo
    echo
); done

cat<<EOF

================================================================================"
⚠️ if you run this script again it will reinit the examples
================================================================================"

blah blah to run the example run 

    supsrc watch

instead of this script.
================================================================================"

EOF
