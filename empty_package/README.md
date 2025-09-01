Setup ~/.pypirc

```
[distutils]
  index-servers =
    pypi

[pypi]
  username = __token__
  password = ...
```

Then do

```
pip install twine wheel setuptools build
cd inspect-ec2-sandbox
python -m build
twine upload dist/*
```
