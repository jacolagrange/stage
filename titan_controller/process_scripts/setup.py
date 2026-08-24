#/bin/env python3

import setuptools

setuptools.setup(
        name="process_scripts",
        version="1.0.0",
        author="Jaime Roelandts",
        author_email="jaime.roelandts@ugent.be",
        description="Package to process the output of titan",
        packages=["process_scripts"],
        classifiers=[
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: MIT License",
            "Operating System :: OS Independent",
        ],
        python_requires='>=3.10',
        install_requires=[
            'matplotlib',
            'numpy',
            'pandas',
            'seaborn',
            'scipy'
        ],
)
