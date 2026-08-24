FROM ubuntu:24.04
# Necessary for tzdata
ENV DEBIAN_FRONTEND=noninteractive
ARG TZ_ARG=UTC
ENV TZ=${TZ_ARG}
RUN apt-get update && apt-get install -y \
    python3 \
    python3-dev \
    python3-venv \
    screen \
    tmux \
    binutils \
 && rm -rf /var/lib/apt/lists/* &&\
 apt-get update && apt-get install -y \
    automake \
    build-essential \
    cmake \
    curl \
    wget \
    libboost-dev \
    libsqlite3-dev \
    zlib1g-dev \
    libbz2-dev \
    liblmdb-dev \
 && rm -rf /var/lib/apt/lists/* &&\
 apt-get update && apt-get install -y \
    autoconf \
    automake \
    autotools-dev \
    bc \
    bison \
    curl \
    device-tree-compiler \
    flex \
    gawk \
    gperf \
    libexpat-dev \
    libgmp-dev \
    libmpc-dev \
    libmpfr-dev \
    libtool \
    libusb-1.0-0-dev \
    patchutils \
    pkg-config \
    texinfo \
    zlib1g-dev \
 && rm -rf /var/lib/apt/lists/* &&\
 apt-get update && apt-get install -y \
    gdb \
    gfortran \
    git \
    g++ \
    vim \
 && rm -rf /var/lib/apt/lists/* &&\
 useradd -m -u 1001 -U slurmslave &&\
 git config --global protocol.file.allow always &&\
 git config --global --add safe.directory "*"
USER slurmslave
RUN git config --global protocol.file.allow always &&\
 git config --global --add safe.directory "*"
