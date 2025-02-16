from setuptools import setup, find_packages

setup(
    name="effortless",
    version="0.1.0",
    author="pysasori",
    author_email="pysasori@gmail.com",
    description="A Python library for automation tasks",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/pysasori/effortless",
    packages=find_packages(),
    install_requires=[
        "requests>=2.26.0",
        "pyautogui>=0.9.53",
        "opencv-python>=4.5.5",
        "numpy>=1.21.0",
        "pytesseract>=0.3.8",
        "psutil>=5.8.0",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)