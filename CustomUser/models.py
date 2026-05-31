from django.db import models
from django.contrib.auth.models import AbstractUser

class CustomUser(AbstractUser):
    employee_code=models.CharField(max_length=10, unique=True,null=True,blank=True,verbose_name="Employee Code")
    job_title=models.CharField(max_length=100,null=True,blank=True,verbose_name="Job Title")

    def __str__(self):
        return f"{self.username}({self.job_title if self.job_title else self.username})"


